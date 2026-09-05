"""求解模型的等价性对照工具。

改造求解路径时，"行为没变"这件事必须是可证明的，不能靠人工审查——之前那条模拟
旁路正是因为只有人工审查，才在同一批候选上给出 12 个里 9 个不一致的判定而无人
察觉。

判据建立在**模型**上，不在解上：喂给 CP-SAT 的模型相同，输出必然相同；而解的
对照是抽样的，且 num_search_workers > 1 时目标值并列的解本来就可能不同。

两级对照：

- ``signature``：顺序无关的规范签名。用于顺序硬化本身——那一步会合法地改变
  字节，此时不能用字节比。签名把变量按名字排序（模型里的变量名都是内容派生的，
  如 presence_t603_i10、stability_t641），约束按内容规范化后取多重集，因此重排
  不影响签名，而增删改一定会体现出来。
- ``digest``：序列化后的 sha256。顺序硬化落地后取基线，此后每一步改造都要求
  完全相同，是 Step 1 之后的硬门禁。

语料来自 schedule_deadline_recommendation_job 表里积累的真实排程参数。回放依赖
scheduler.replayable_kwargs 记录的完整入参，current_project_id 不在回放范围内
（由作业按候选项目设置），要从作业行本身取。

用法::

    python tools/model_equivalence.py capture  baseline.json
    python tools/model_equivalence.py compare  baseline.json           # 规范签名
    python tools/model_equivalence.py compare  baseline.json --bytes   # 逐字节
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ortools.sat.python import cp_model  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402


# 对照必须在同一个时间原点下进行，否则每个时间单元都不同。取一个固定的、
# 落在业务数据范围内的时刻。
PINNED_NOW = datetime(2026, 9, 5, 9, 0, 0)


class _ModelCaptured(Exception):
    """在求解前截住模型。对照只关心模型，不需要真的解。"""

    def __init__(self, proto, raw: bytes):
        self.proto = proto
        self.raw = raw


def model_bytes(model):
    """导出一个 CpModel 的真实字节，并解析成可反射遍历的 protobuf 消息。

    返回 (消息, 原始字节)。语料采集与等价性对照共用它。
    """
    return _parse_model(model)


def _parse_model(model):
    """把 CpModel 转成可反射遍历的纯 Python protobuf 消息。

    ortools 这个版本的 model.Proto() 交回的是 C++ 包装对象，既不能序列化也没有
    protobuf 的反射接口，规范化用不了。CpModel.ExportToFile 能写出真实的二进制
    proto，读回来用 cp_model_pb2 解析即可。
    """
    from ortools.sat import cp_model_pb2

    with tempfile.NamedTemporaryFile(suffix=".pb", delete=False) as handle:
        path = handle.name
    try:
        if not model.ExportToFile(path):
            raise RuntimeError("导出求解模型失败")
        raw = Path(path).read_bytes()
    finally:
        Path(path).unlink(missing_ok=True)
    parsed = cp_model_pb2.CpModelProto()
    parsed.ParseFromString(raw)
    # protobuf 消息不允许挂自定义属性，原始字节单独带出去。
    return parsed, raw


def load_corpus(db, limit: int | None = None) -> list[dict]:
    """从历史作业里取出可回放的真实排程场景。"""
    from app.services.schedule_deadline_recommendation_job_service import (
        _deserialize_generate_kwargs,
    )

    rows = db.execute(text(
        "SELECT id, project_id, payload FROM schedule_deadline_recommendation_job"
        " ORDER BY created_at DESC, id DESC"
    )).fetchall()
    corpus = []
    for job_id, project_id, payload in rows:
        payload = payload if isinstance(payload, dict) else json.loads(payload)
        raw = (payload or {}).get("generate_kwargs")
        if not raw:
            continue
        kwargs = _deserialize_generate_kwargs(raw)
        kwargs["current_project_id"] = project_id
        kwargs["commit"] = False
        kwargs["emit_advance_notifications"] = False
        corpus.append({"id": str(job_id), "kwargs": kwargs})
        if limit and len(corpus) >= limit:
            break
    return corpus


def capture_model(db, generate_kwargs: dict, now: datetime = PINNED_NOW):
    """构造一次求解模型并在求解前截住它。

    返回 ((proto, 原始字节), 提前返回的结果)。有些场景本来就在建模前返回（例如任务全在等
    签批），那种情况 proto 为 None，而它返回的状态同样要参与对照。

    全程包在 savepoint 里回滚：建模过程本身会补写工作日历。
    """
    import app.services.scheduler as scheduler_module

    class _PinnedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    real_solve = cp_model.CpSolver.Solve
    real_datetime = scheduler_module.datetime

    def _capturing_solve(self, model, *args, **kwargs):
        raise _ModelCaptured(*_parse_model(model))

    cp_model.CpSolver.Solve = _capturing_solve
    scheduler_module.datetime = _PinnedDatetime
    savepoint = db.begin_nested()
    try:
        result = scheduler_module.SchedulerService(db)._generate(**generate_kwargs)
        return None, result
    except _ModelCaptured as captured:
        return (captured.proto, captured.raw), None
    finally:
        savepoint.rollback()
        cp_model.CpSolver.Solve = real_solve
        scheduler_module.datetime = real_datetime


def _variable_names(proto) -> list[str]:
    return [variable.name for variable in proto.variables]


# proto 里对变量的引用散布在这些字段（字面量用 -index-1 表示取反）；
# 对区间的引用在 intervals 字段里，指的是"第几条约束"。
_VARIABLE_REFERENCE_FIELDS = {"vars", "literals", "enforcement_literal", "target"}
_INTERVAL_REFERENCE_FIELDS = {"intervals"}


def _canonical_constraint(constraint, variable_rank: dict, interval_rank: dict) -> str:
    """把一条约束渲染成与创建顺序无关的字符串。

    索引是按创建顺序分配的，所以直接渲染 proto 得到的文本随重排而变。这里先把
    所有变量/区间引用换成"按名字排序后的序号"——名字是内容派生的（presence_t603_i10、
    stability_t641、fixed_slot_7102…），于是重排不影响结果，而任何增删改都会体现出来。
    """
    from google.protobuf import text_format

    canonical = type(constraint)()
    canonical.CopyFrom(constraint)
    _remap(canonical, variable_rank, interval_rank)
    _sort_commutative(canonical)
    return text_format.MessageToString(canonical, as_one_line=True)


# 这几个字段的元素之间是可交换的：字面量的合取/析取、区间的互斥集合，换个次序
# 是同一个约束。重映射之后还要把它们排序，否则集合相同、次序不同也会被判成有差异
# ——实测倒序仪器时 exactly_one 的字面量就是这种情况（8,9,10 对 10,9,8）。
#
# 不排 vars / coeffs：它们是位置配对的两个数组，单独排一个会把约束改坏。
_COMMUTATIVE_FIELDS = {"literals", "enforcement_literal", "intervals"}
# 这些是可交换的**子消息**列表：取最大值的各个参数换次序仍是同一个约束。
# 排序键取子消息规范化之后的文本。
_COMMUTATIVE_MESSAGE_FIELDS = {"exprs"}


def _sort_commutative(message) -> None:
    for descriptor, value in message.ListFields():
        if descriptor.type == descriptor.TYPE_MESSAGE:
            items = value if descriptor.is_repeated else [value]
            for item in items:
                _sort_commutative(item)
            if descriptor.is_repeated and descriptor.name in _COMMUTATIVE_MESSAGE_FIELDS:
                from google.protobuf import text_format as _tf

                ordered = sorted(
                    list(value), key=lambda item: _tf.MessageToString(item, as_one_line=True),
                )
                copies = [type(item)() for item in ordered]
                for target, source in zip(copies, ordered):
                    target.CopyFrom(source)
                del value[:]
                value.extend(copies)
        elif descriptor.is_repeated and descriptor.name in _COMMUTATIVE_FIELDS:
            ordered = sorted(value)
            del value[:]
            value.extend(ordered)
    _sort_linear_terms(message)


def _sort_linear_terms(message) -> None:
    """线性项按 (变量, 系数) 成对排序。

    vars 与 coeffs 是位置配对的两个数组，表示一个求和式——加数换个次序是同一个
    式子，但渲染出来的文本不同。必须成对排，单独排任何一个都会把约束改坏。
    """
    names = {descriptor.name for descriptor, _ in message.ListFields()}
    if not {"vars", "coeffs"} <= names:
        return
    variables = list(message.vars)
    coefficients = list(message.coeffs)
    if len(variables) != len(coefficients) or len(variables) < 2:
        return
    pairs = sorted(zip(variables, coefficients))
    del message.vars[:]
    del message.coeffs[:]
    message.vars.extend(item[0] for item in pairs)
    message.coeffs.extend(item[1] for item in pairs)


def _remap(message, variable_rank: dict, interval_rank: dict) -> None:
    for descriptor, value in list(message.ListFields()):
        if descriptor.type == descriptor.TYPE_MESSAGE:
            items = value if descriptor.is_repeated else [value]
            for item in items:
                _remap(item, variable_rank, interval_rank)
        elif descriptor.name in _VARIABLE_REFERENCE_FIELDS:
            _rewrite(message, descriptor, value, variable_rank, signed=True)
        elif descriptor.name in _INTERVAL_REFERENCE_FIELDS:
            _rewrite(message, descriptor, value, interval_rank, signed=False)


def _rewrite(message, descriptor, value, rank: dict, signed: bool) -> None:
    def mapped(item: int) -> int:
        if signed and item < 0:
            # 取反的字面量：-index-1
            return -rank.get(-item - 1, -item - 1) - 1
        return rank.get(item, item)

    if descriptor.is_repeated:
        remapped = [mapped(item) for item in value]
        del value[:]
        value.extend(remapped)
    else:
        setattr(message, descriptor.name, mapped(value))


def signature(proto) -> dict:
    """顺序无关的规范签名。"""
    names = [variable.name for variable in proto.variables]
    anonymous = sum(1 for name in names if not name)
    duplicated = len(names) - len(set(names))
    # 变量按 (名字, 定义域) 排序后的位次，作为它在规范形式里的编号。
    variable_rank = {
        index: rank for rank, index in enumerate(sorted(
            range(len(proto.variables)),
            key=lambda i: (proto.variables[i].name, list(proto.variables[i].domain)),
        ))
    }
    interval_indexes = [
        index for index, item in enumerate(proto.constraints)
        if item.WhichOneof("constraint") == "interval"
    ]
    interval_rank = {
        index: rank for rank, index in enumerate(sorted(
            interval_indexes, key=lambda i: proto.constraints[i].name,
        ))
    }
    variables = sorted(
        "%s|%s" % (variable.name, list(variable.domain))
        for variable in proto.variables
    )
    constraints = Counter(
        _canonical_constraint(constraint, variable_rank, interval_rank)
        for constraint in proto.constraints
    )
    objective = sorted(
        "%s*%s" % (names[index] if 0 <= index < len(names) else index, coefficient)
        for index, coefficient in zip(proto.objective.vars, proto.objective.coeffs)
    ) if proto.HasField("objective") else []
    return {
        # 名字不唯一就说明"按名字规范化"这个前提不成立，必须当场知道。
        "anonymous_variables": anonymous,
        "duplicated_variable_names": duplicated,
        "variables": variables,
        "constraints": sorted(constraints.items()),
        "objective": objective,
        "objective_offset": proto.objective.offset if proto.HasField("objective") else 0,
    }


def signature_digest(proto) -> str:
    payload = json.dumps(signature(proto), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def byte_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_report(limit: int | None = None) -> dict:
    db = SessionLocal()
    try:
        entries = {}
        for case in load_corpus(db, limit):
            captured, early = capture_model(db, case["kwargs"])
            if captured is None:
                entries[case["id"]] = {
                    "model": None,
                    "early_return": (early or {}).get("message") or (early or {}).get("status"),
                }
                continue
            proto, raw = captured
            entries[case["id"]] = {
                "model": {
                    "variables": len(proto.variables),
                    "constraints": len(proto.constraints),
                    "signature": signature_digest(proto),
                    "bytes": byte_digest(raw),
                },
                "early_return": None,
            }
        return entries
    finally:
        db.close()


def _compare(baseline: dict, current: dict, use_bytes: bool) -> int:
    key = "bytes" if use_bytes else "signature"
    label = "逐字节" if use_bytes else "规范签名"
    missing = sorted(set(baseline) - set(current))
    added = sorted(set(current) - set(baseline))
    differing = []
    for case_id in sorted(set(baseline) & set(current)):
        before, after = baseline[case_id], current[case_id]
        if (before["model"] is None) != (after["model"] is None):
            differing.append((case_id, "一侧建模、另一侧提前返回"))
        elif before["model"] is None:
            if before["early_return"] != after["early_return"]:
                differing.append((case_id, "提前返回的结论不同：%s → %s" % (
                    before["early_return"], after["early_return"])))
        elif before["model"][key] != after["model"][key]:
            differing.append((case_id, "%s 不同（变量 %d→%d，约束 %d→%d）" % (
                label,
                before["model"]["variables"], after["model"]["variables"],
                before["model"]["constraints"], after["model"]["constraints"])))
    total = len(set(baseline) & set(current))
    print("对照 %d 个场景（%s）" % (total, label))
    if missing:
        print("  基线里有、现在取不到：%s" % ", ".join(missing))
    if added:
        print("  现在多出来的：%s" % ", ".join(added))
    for case_id, reason in differing:
        print("  ✗ %s  %s" % (case_id, reason))
    if not differing and not missing and not added:
        print("  ✓ 全部等价")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "compare"])
    parser.add_argument("path")
    parser.add_argument("--bytes", action="store_true", help="按逐字节而非规范签名对照")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    report = build_report(args.limit)
    if args.action == "capture":
        Path(args.path).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        modelled = sum(1 for item in report.values() if item["model"])
        print("已记录 %d 个场景（其中 %d 个建出了模型）→ %s" % (
            len(report), modelled, args.path))
        return 0
    baseline = json.loads(Path(args.path).read_text(encoding="utf-8"))
    return _compare(baseline, report, args.bytes)


if __name__ == "__main__":
    raise SystemExit(main())

"""确定性的求解模型语料。

改造求解路径时要能证明"喂给求解器的那道题没变"，判据是模型序列化后逐字节相同。
这就需要一批可复现的场景。

前两次尝试都不成立，教训记在这里：

- 取自 schedule_deadline_recommendation_job 表：它只记录"排程失败并触发了建议搜索"
  的场景，正常重排、插单、仪器故障、暂停切换、签批流程一个都没有；而且依赖生产
  数据，线上状态一变，能建出模型的场景就从 28 个塌到 9 个。采集它还得连生产库——
  正是这样误落过一次真实重排。
- 挂在 pytest 上采集整轮测试的模型：模型的每个时间单元都以"现在"为原点，不冻时钟
  就没有可比性；而冻住全局时钟会让 91 个测试挂掉（很多测试依赖真实时间推进）。

现在的做法：场景全部用**固定日期**在内存 SQLite 里现搭，时钟由 _generate 的 now
参数显式指定。不碰生产库，不受挂钟影响，跑多少次都一样。

用法::

    python tools/model_corpus.py capture  corpus.json
    python tools/model_corpus.py compare  corpus.json           # 规范签名
    python tools/model_corpus.py compare  corpus.json --bytes   # 逐字节
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ortools.sat.python import cp_model  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    Instrument,
    InstrumentCapability,
    InstrumentFault,
    MaintenanceWindow,
    Milestone,
    Project,
    Task,
    TaskDependency,
    TimeSlot,
)

# 场景里的一切时间都从这一刻起算，包括求解的时间原点。
NOW = datetime(2026, 9, 7, 9, 0, 0)
DAY = timedelta(days=1)


class _ModelCaptured(Exception):
    def __init__(self, proto, raw):
        self.proto = proto
        self.raw = raw


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _instrument(db, code, *, status="idle", tags=()):
    instrument = Instrument(
        code=code, name=code, availability_status="available", status=status,
        effective_work_start="08:30", effective_work_end="20:00",
    )
    db.add(instrument)
    db.flush()
    for name, value in tags:
        db.add(InstrumentCapability(
            instrument_id=instrument.id, tag_name=name, tag_value=value,
        ))
    db.flush()
    return instrument


def _project(db, code, *, priority=3, start=None, end=None):
    project = Project(
        code=code, name=code, priority=priority,
        start_date=start or NOW - DAY, end_date=end or NOW + 30 * DAY,
    )
    db.add(project)
    db.flush()
    return project


def _task(db, project, name, **kwargs):
    task = Task(
        project_id=project.id, name=name, task_type=kwargs.pop("task_type", "test"),
        status=kwargs.pop("status", "pending"),
        est_duration_hours=kwargs.pop("hours", 4),
        requires_instrument=kwargs.pop("requires_instrument", False),
        requires_human=kwargs.pop("requires_human", False),
        **kwargs,
    )
    db.add(task)
    db.flush()
    return task


def _slot(db, task, instrument, start, hours, **kwargs):
    slot = TimeSlot(
        task_id=task.id, schedule_run_id=kwargs.pop("run", "seed"),
        instrument_id=instrument.id if instrument else None,
        plan_start=start, plan_end=start + timedelta(hours=hours),
        tier=kwargs.pop("tier", "confirmed"),
        status=kwargs.pop("status", "scheduled"),
        lifecycle_status="active", **kwargs,
    )
    db.add(slot)
    db.flush()
    return slot


# --- 场景 -------------------------------------------------------------------
# 每个场景返回 (db, generate_kwargs)。加场景就是往这个表里加一条。

def scenario_single_instrument_task():
    """最小形态：一个任务，三台候选仪器。

    候选仪器要多于一台，否则仪器顺序变化对模型毫无影响，"顺序进模型"这件事就
    验不到——语料第一版正是这样，倒序仪器只有 13 个场景里的 1 个报差。
    """
    db = _session()
    instruments = [_instrument(db, "INST-%s" % code) for code in "ABC"]
    project = _project(db, "P-SINGLE")
    task = _task(db, project, "方法开发", requires_instrument=True,
                 instrument_ids=[item.id for item in instruments])
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_chain_with_dependencies():
    """三个任务串成依赖链，走前置约束与依赖间隔惩罚。"""
    db = _session()
    instruments = [_instrument(db, "INST-%s" % code) for code in "AB"]
    project = _project(db, "P-CHAIN")
    tasks = [
        _task(db, project, "任务%d" % index, requires_instrument=True,
              instrument_ids=[item.id for item in instruments], hours=3)
        for index in range(3)
    ]
    db.add(TaskDependency(task_id=tasks[1].id, predecessor_id=tasks[0].id))
    db.add(TaskDependency(task_id=tasks[2].id, predecessor_id=tasks[1].id))
    db.commit()
    return db, {"current_project_id": project.id,
                "task_ids": [task.id for task in tasks]}


def scenario_competing_projects_on_one_instrument():
    """两个项目抢同一台仪器，走跨项目切换与仪器产能约束。"""
    db = _session()
    instruments = [_instrument(db, "INST-%s" % code) for code in "AB"]
    ids = [item.id for item in instruments]
    high = _project(db, "P-HIGH", priority=1)
    low = _project(db, "P-LOW", priority=8)
    tasks = [
        _task(db, high, "高优任务", requires_instrument=True,
              instrument_ids=ids, hours=6),
        _task(db, low, "低优任务", requires_instrument=True,
              instrument_ids=ids, hours=6),
    ]
    db.commit()
    return db, {"current_project_id": high.id,
                "task_ids": [task.id for task in tasks]}


def scenario_human_capacity():
    """同一个负责人的两个非仪器任务，走人员产能约束。"""
    db = _session()
    _instrument(db, "INST-A")   # 预检要求至少有一台可用仪器
    project = _project(db, "P-HUMAN")
    tasks = [
        _task(db, project, "方案撰写", requires_human=True, assignee_id=1, hours=3),
        _task(db, project, "报告撰写", requires_human=True, assignee_id=1, hours=3),
    ]
    db.commit()
    return db, {"current_project_id": project.id,
                "task_ids": [task.id for task in tasks]}


def scenario_existing_slots_are_fixed():
    """别的项目已有时间槽占着仪器，走固定槽的产能扣减。"""
    db = _session()
    instrument = _instrument(db, "INST-A")
    other = _project(db, "P-OTHER")
    occupied = _task(db, other, "占位任务", status="scheduled",
                     requires_instrument=True, instrument_ids=[instrument.id])
    _slot(db, occupied, instrument, NOW + 2 * DAY, 5)
    project = _project(db, "P-NEW")
    task = _task(db, project, "新任务", requires_instrument=True,
                 instrument_ids=[instrument.id], hours=5)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_released_slots():
    """重排：自己原有的槽让位，走 released_slot_ids 这条路径。"""
    db = _session()
    instruments = [_instrument(db, "INST-%s" % code) for code in "AB"]
    instrument = instruments[0]
    project = _project(db, "P-RELEASE")
    task = _task(db, project, "重排任务", requires_instrument=True,
                 instrument_ids=[item.id for item in instruments], hours=4)
    slot = _slot(db, task, instrument, NOW + 3 * DAY, 4)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id],
                "released_slot_ids": {slot.id}}


def scenario_split_task():
    """可分片任务，走分片单元变量那条分支。"""
    db = _session()
    instruments = [_instrument(db, "INST-%s" % code) for code in "AB"]
    project = _project(db, "P-SPLIT")
    task = _task(db, project, "长任务", requires_instrument=True,
                 instrument_ids=[item.id for item in instruments],
                 hours=20, allow_split=True)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_maintenance_window():
    """仪器有维护窗口，走维护规避。"""
    db = _session()
    instrument = _instrument(db, "INST-A")
    db.add(MaintenanceWindow(
        instrument_id=instrument.id, mw_type="planned",
        start_time=NOW + 2 * DAY, end_time=NOW + 3 * DAY,
    ))
    project = _project(db, "P-MAINT")
    task = _task(db, project, "受维护影响的任务", requires_instrument=True,
                 instrument_ids=[instrument.id], hours=6)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_instrument_fault():
    """仪器故障期间不可用，故障窗口按维护窗口处理。"""
    db = _session()
    instrument = _instrument(db, "INST-A", status="fault")
    db.add(InstrumentFault(
        instrument_id=instrument.id, status="open",
        reported_at=NOW, estimated_resolved_at=NOW + 2 * DAY,
        description="语料用故障",
    ))
    project = _project(db, "P-FAULT")
    task = _task(db, project, "故障后任务", requires_instrument=True,
                 instrument_ids=[instrument.id], hours=4)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_capability_matching():
    """按能力标签匹配仪器：两台仪器只有一台合格。"""
    db = _session()
    _instrument(db, "INST-PLAIN")
    good = _instrument(db, "INST-LCMS", tags=[("检测类型", "LCMS")])
    project = _project(db, "P-CAP")
    task = _task(db, project, "需要能力的任务", requires_instrument=True,
                 instrument_ids=[good.id], hours=4)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_paused_task_keeps_status():
    """暂停任务参与重排：位置可动，状态要保住。"""
    db = _session()
    instrument = _instrument(db, "INST-A")
    project = _project(db, "P-PAUSED")
    task = _task(db, project, "暂停任务", status="paused",
                 requires_instrument=True, instrument_ids=[instrument.id], hours=4)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id],
                "preserved_status_task_ids": {task.id}}


def scenario_milestone_deadline():
    """任务挂在里程碑上，走里程碑到期惩罚。"""
    db = _session()
    instrument = _instrument(db, "INST-A")
    project = _project(db, "P-MILESTONE")
    milestone = Milestone(project_id=project.id, name="中期节点",
                          due_date=NOW + 10 * DAY)
    db.add(milestone)
    db.flush()
    task = _task(db, project, "里程碑任务", requires_instrument=True,
                 instrument_ids=[instrument.id], hours=4, milestone_id=milestone.id)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_window_too_short_fails():
    """时间窗装不下——走失败诊断分支。"""
    db = _session()
    instrument = _instrument(db, "INST-A")
    project = _project(db, "P-TIGHT", start=NOW, end=NOW + 1 * DAY)
    task = _task(db, project, "排不下的任务", requires_instrument=True,
                 instrument_ids=[instrument.id], hours=80)
    db.commit()
    return db, {"current_project_id": project.id, "task_ids": [task.id]}


def scenario_solver_infeasible_runs_diagnostics():
    """求解器真的判不可行——走深度失败诊断。

    与 window_too_short_fails 不同：那个在建变量阶段就因单任务窗口不足返回了，
    根本到不了诊断。这里每个任务单独都排得下，串起来才装不下，只有求解器能发现。
    诊断路径要从任务反向拿项目全量任务、上溯父链、读时间槽，是任务值对象化风险
    最集中的地方，必须有场景覆盖。
    """
    db = _session()
    instruments = [_instrument(db, "INST-%s" % code) for code in "AB"]
    ids = [item.id for item in instruments]
    # 让别的项目先占掉一段仪器时间：失败诊断要读 slot.task.project.code 之类，
    # 没有固定槽的话那条路根本走不到，验证就是空的。
    other = _project(db, "P-OCCUPY")
    occupied = _task(db, other, "占位任务", status="scheduled",
                     requires_instrument=True, instrument_ids=[ids[0]], hours=6)
    _slot(db, occupied, instruments[0], NOW + 1 * DAY, 6)
    project = _project(db, "P-INFEASIBLE", start=NOW, end=NOW + 4 * DAY)
    first = _task(db, project, "前序任务", requires_instrument=True,
                  instrument_ids=ids, hours=30)
    second = _task(db, project, "后续任务", requires_instrument=True,
                   instrument_ids=ids, hours=30)
    db.add(TaskDependency(task_id=second.id, predecessor_id=first.id))
    db.commit()
    return db, {"current_project_id": project.id,
                "task_ids": [first.id, second.id]}


def scenario_stability_against_original_windows():
    """带原计划窗口重排，走稳定性惩罚。"""
    db = _session()
    instrument = _instrument(db, "INST-A")
    project = _project(db, "P-STABLE")
    task = _task(db, project, "稳定性任务",
                 requires_instrument=True, instrument_ids=[instrument.id], hours=4)
    db.commit()
    return db, {
        "current_project_id": project.id, "task_ids": [task.id],
        "stability_task_ids": {task.id},
        "original_schedule_windows": {task.id: (NOW + 4 * DAY, NOW + 4 * DAY + timedelta(hours=4))},
    }


SCENARIOS = {
    name[len("scenario_"):]: value
    for name, value in sorted(globals().items())
    if name.startswith("scenario_")
}


# --- 采集与对照 -------------------------------------------------------------

def capture_one(builder) -> dict:
    from tools.model_equivalence import byte_digest, model_bytes, signature_digest

    db, kwargs = builder()
    real_solve = cp_model.CpSolver.Solve

    def _capturing(solver, model, *args, **kwargs_):
        proto, raw = model_bytes(model)
        raise _ModelCaptured(proto, raw)

    cp_model.CpSolver.Solve = _capturing
    try:
        from app.services.scheduler import SchedulerService

        result = SchedulerService(db)._generate(
            now=NOW, commit=False, emit_advance_notifications=False, **kwargs,
        )
        return {"model": None,
                "early_return": (result or {}).get("message") or (result or {}).get("status")}
    except _ModelCaptured as captured:
        return {"model": {
            "variables": len(captured.proto.variables),
            "constraints": len(captured.proto.constraints),
            "signature": signature_digest(captured.proto),
            "bytes": byte_digest(captured.raw),
        }, "early_return": None}
    except Exception as exc:
        return {"model": None, "early_return": "异常 %r" % (exc,)}
    finally:
        cp_model.CpSolver.Solve = real_solve
        db.close()


def build_report() -> dict:
    return {name: capture_one(builder) for name, builder in SCENARIOS.items()}


def _compare(baseline: dict, current: dict, use_bytes: bool) -> int:
    key = "bytes" if use_bytes else "signature"
    label = "逐字节" if use_bytes else "规范签名"
    problems = []
    for name in sorted(set(baseline) | set(current)):
        before, after = baseline.get(name), current.get(name)
        if before is None or after is None:
            problems.append((name, "只在一侧存在"))
        elif (before["model"] is None) != (after["model"] is None):
            problems.append((name, "一侧建模、另一侧提前返回：%s / %s" % (
                before["early_return"], after["early_return"])))
        elif before["model"] is None:
            if before["early_return"] != after["early_return"]:
                problems.append((name, "结论不同：%s → %s" % (
                    before["early_return"], after["early_return"])))
        elif before["model"][key] != after["model"][key]:
            problems.append((name, "%s 不同（变量 %d→%d 约束 %d→%d）" % (
                label, before["model"]["variables"], after["model"]["variables"],
                before["model"]["constraints"], after["model"]["constraints"])))
    modelled = sum(1 for item in current.values() if item["model"])
    print("对照 %d 个场景（%s），其中 %d 个建出了模型" % (len(current), label, modelled))
    for name, reason in problems:
        print("  ✗ %s  %s" % (name, reason))
    if not problems:
        print("  ✓ 全部等价")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "compare", "list"])
    parser.add_argument("path", nargs="?")
    parser.add_argument("--bytes", action="store_true")
    args = parser.parse_args()

    if args.action == "list":
        for name in SCENARIOS:
            print(" ", name)
        return 0
    report = build_report()
    if args.action == "capture":
        Path(args.path).write_text(
            json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        modelled = sum(1 for item in report.values() if item["model"])
        print("已记录 %d 个场景（其中 %d 个建出了模型）→ %s" % (
            len(report), modelled, args.path))
        for name, item in sorted(report.items()):
            if item["model"] is None:
                print("   %-36s 提前返回：%s" % (name, item["early_return"]))
        return 0
    baseline = json.loads(Path(args.path).read_text(encoding="utf-8"))
    return _compare(baseline, report, args.bytes)


if __name__ == "__main__":
    raise SystemExit(main())

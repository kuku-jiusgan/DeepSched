from __future__ import annotations

from datetime import datetime, timedelta
from heapq import heappop, heappush
from itertools import combinations
from time import monotonic

from app.models import Project, Task

MAX_RECOMMENDATIONS = 20
SEARCH_TIME_LIMIT_SECONDS = 120

FEASIBLE = "feasible"          # 求解器找到了可行排程
INFEASIBLE = "infeasible"      # 求解器证明了排不下
UNDETERMINED = "undetermined"  # 求解超时，什么也没证明


def enumerate_verified_date_adjustments(
    db, scheduler, project_ids: list[int], original_deadlines: dict[int, datetime],
    horizon_end: datetime, generate_kwargs: dict, project_labels: dict[int, str] | None = None,
) -> list[dict]:
    """Enumerate minimal project-deadline adjustments verified by the solver."""
    project_ids = [project_id for project_id in project_ids if project_id in original_deadlines]
    priorities = _load_project_priorities(db, project_ids)
    candidates = _candidate_deadlines(project_ids, original_deadlines, horizon_end)
    search_deadline = monotonic() + SEARCH_TIME_LIMIT_SECONDS
    if not _any_adjustment_can_help(db, scheduler, project_ids, candidates, generate_kwargs):
        return []
    results: list[dict] = []
    search_order = _search_order(project_ids, original_deadlines)
    for size in range(1, len(project_ids) + 1):
        for selected in combinations(search_order, size):
            if monotonic() >= search_deadline:
                return _sort_results(results)
            if any(set(item["projects"]) < set(selected) for item in results):
                continue
            adjustment = _first_verified_adjustment(
                db, scheduler, selected, candidates, generate_kwargs, search_deadline,
            )
            if adjustment:
                results.append(_format_adjustment(
                    adjustment, original_deadlines, project_labels or {}, priorities,
                ))
                if len(results) >= MAX_RECOMMENDATIONS:
                    return _sort_results(results)
        # 改一个项目的合同日永远优于改两个，前端也把多项目方案标成"需要一起
        # 调整"的退让选项。这一档已经有方案，再往下搜只会得到更差的组合，却要
        # 把用户晾在"计算中"里等满整个预算。
        if results:
            break
    return _sort_results(results)


def _candidate_deadlines(
    project_ids: list[int], original_deadlines: dict[int, datetime], horizon_end: datetime,
) -> dict[int, list[datetime]]:
    return {
        project_id: [
            (original_deadlines[project_id] + timedelta(days=offset)).replace(hour=23, minute=59, second=0, microsecond=0)
            for offset in range(1, (horizon_end.date() - original_deadlines[project_id].date()).days + 1)
            if (original_deadlines[project_id] + timedelta(days=offset)).date() <= horizon_end.date()
        ]
        for project_id in project_ids
    }


def _any_adjustment_can_help(db, scheduler, project_ids, candidates, generate_kwargs) -> bool:
    """一次求解判断"延期"这条路在候选范围内是否走得通。

    延后结题日只放宽任务的完工上界，是单调放松：把每个候选项目都推到最远的候选
    日期，就是整个搜索空间里最宽松的一种改法。这一步被证明排不下，后面成百上千
    次组合试探必然全部失败。曾经因此空转满 120 秒，最后只给出一张空白的方案表，
    还顺带把重排请求锁在后面等超时。

    只有拿到不可行的证明才放弃：超时未决时照常往下搜，宁可多花时间也不能把
    存在的方案漏掉。
    """
    extreme = {
        project_id: candidates[project_id][-1]
        for project_id in project_ids if candidates[project_id]
    }
    if not extreme:
        return False
    return _probe_deadlines(db, scheduler, extreme, generate_kwargs) != INFEASIBLE


def _search_order(project_ids: list[int], original_deadlines: dict[int, datetime]) -> list[int]:
    """按结题日从早到晚试。

    卡住排程的通常是结题日最早的那个项目——它的任务被顶到期限之外，别的项目
    延多久都腾不出它需要的位置。此前按项目号顺序试，真正该延的项目排在后面时，
    要先在前面的项目上白试上百次。
    """
    return sorted(project_ids, key=lambda project_id: (original_deadlines[project_id], project_id))


def _first_verified_adjustment(
    db, scheduler, selected, candidates, generate_kwargs, search_deadline,
):
    if any(not candidates[project_id] for project_id in selected):
        return None
    initial = tuple(0 for _ in selected)
    queue = [(len(selected), initial)]
    visited = {initial}
    while queue:
        if monotonic() >= search_deadline:
            return None
        _cost, indexes = heappop(queue)
        adjustment = {
            project_id: candidates[project_id][indexes[index]]
            for index, project_id in enumerate(selected)
        }
        if _probe_deadlines(db, scheduler, adjustment, generate_kwargs) == FEASIBLE:
            return adjustment
        for index, project_id in enumerate(selected):
            next_indexes = list(indexes)
            next_indexes[index] += 1
            candidate_indexes = tuple(next_indexes)
            if (
                candidate_indexes not in visited
                and next_indexes[index] < len(candidates[project_id])
            ):
                visited.add(candidate_indexes)
                heappush(queue, (sum(item + 1 for item in candidate_indexes), candidate_indexes))
    return None


def _load_project_priorities(db, project_ids: list[int]) -> dict[int, int]:
    query = getattr(db, "query", None)
    if query is None:
        return {}
    return {
        project.id: int(project.priority or 999)
        for project in query(Project).filter(Project.id.in_(project_ids)).all()
    }


def _release_replan_tasks(db, task_ids) -> None:
    """把待重排任务恢复成"待排"，与真实重排的前置处理保持一致。

    _execute_replan 在求解前会把这些任务置为 pending 再释放时间槽。验证若不做
    这一步，任务仍挂着 scheduled，求解器当它们已经定死不能动——于是"延期另一个
    项目给本项目腾时间"这类方案会被系统性误判为不可行，无论延多久都排不下，
    用户因此拿不到本来可选的方案，搜索也要把候选日期全部白跑一遍。
    """
    if not task_ids:
        return
    db.query(Task).filter(
        Task.id.in_(list(task_ids)),
        Task.status.in_(["scheduled", "blocked", "interrupted"]),
    ).update({"status": "pending"}, synchronize_session=False)


def _probe_deadlines(db, scheduler, deadlines: dict[int, datetime], generate_kwargs: dict) -> str:
    """在给定的一组结题日下试解一次，返回三态判定。

    必须把"求解器证明了排不下"和"5 秒内没算出来"分开：后者什么都没证明。
    最宽松那次探测放开了全部结题日上界，模型反而更难收敛，实测就会超时返回
    UNKNOWN——若把它当成排不下，本来存在的调整方案会被整批丢掉。
    """
    savepoint = db.begin_nested()
    try:
        for project_id, deadline in deadlines.items():
            project = db.query(Project).filter(Project.id == project_id).one()
            project.end_date = deadline
        _release_replan_tasks(db, generate_kwargs.get("task_ids"))
        db.flush()
        result = scheduler.generate(
            **generate_kwargs, commit=False, emit_advance_notifications=False,
            include_failure_diagnostics=False, solver_time_limit=5.0,
            feasibility_only=True,
        )
    finally:
        savepoint.rollback()
    if result.get("status") == "ok":
        return FEASIBLE
    return INFEASIBLE if result.get("solver_status") == "INFEASIBLE" else UNDETERMINED


def _format_adjustment(
    adjustment: dict[int, datetime], originals: dict[int, datetime], labels: dict[int, str],
    priorities: dict[int, int] | None = None,
) -> dict:
    changes = [
        {
            "project_id": project_id,
            "project_label": labels.get(project_id, f"项目 {project_id}"),
            "original_deadline": originals[project_id].strftime("%Y-%m-%d"),
            "suggested_deadline": deadline.strftime("%Y-%m-%d"),
            "delay_days": (deadline.date() - originals[project_id].date()).days,
            "project_priority": (priorities or {}).get(project_id, 999),
        }
        for project_id, deadline in sorted(adjustment.items())
    ]
    return {
        "code": "",
        "kind": "verified_date_adjustment",
        "title": "、".join(f"{item['project_label']} 延期 {item['delay_days']} 天" for item in changes),
        "description": "；".join(
            f"{item['project_label']}结题日 {item['original_deadline']} → {item['suggested_deadline']}"
            for item in changes
        ),
        "projects": [item["project_id"] for item in changes],
        "changes": changes,
        "verified": True,
        "verification": "solver",
    }


def _sort_results(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda item: (
        tuple(sorted(
            (-next(change["project_priority"] for change in item["changes"] if change["project_id"] == project_id), project_id)
            for project_id in item["projects"]
        )),
        len(item["projects"]), sum(change["delay_days"] for change in item["changes"]),
        tuple(change["suggested_deadline"] for change in item["changes"]),
    ))[:MAX_RECOMMENDATIONS]

from __future__ import annotations

from datetime import datetime, timedelta
from heapq import heappop, heappush
from time import monotonic
from app.models import Project
from app.services.schedule_snapshot import SimulationContext

MAX_RECOMMENDATIONS = 20
SEARCH_TIME_LIMIT_SECONDS = 120

FEASIBLE = "feasible"          # 求解器找到了可行排程
INFEASIBLE = "infeasible"      # 求解器证明了排不下
UNDETERMINED = "undetermined"  # 求解超时，什么也没证明


def enumerate_verified_date_adjustments(
    db, scheduler, project_ids: list[int], original_deadlines: dict[int, datetime],
    horizon_end: datetime, generate_kwargs: dict, project_labels: dict[int, str] | None = None,
    simulation_context: SimulationContext | None = None,
) -> list[dict]:
    """Enumerate minimal project-deadline adjustments verified by the solver."""
    project_ids = [project_id for project_id in project_ids if project_id in original_deadlines]
    priorities = _load_project_priorities(db, project_ids)
    candidates = _candidate_deadlines(project_ids, original_deadlines, horizon_end)
    search_deadline = monotonic() + SEARCH_TIME_LIMIT_SECONDS
    if simulation_context is None:
        can_help = _any_adjustment_can_help(db, scheduler, project_ids, candidates, generate_kwargs)
    else:
        can_help = _any_adjustment_can_help(
            db, scheduler, project_ids, candidates, generate_kwargs, simulation_context,
        )
    if not can_help:
        return []
    results: list[dict] = []
    # 每个项目单独出一套方案：只动它一个，其余项目原地不动，求它最短要延几天。
    # 方案之间互相独立，不存在"这 2 个项目需要一起调整"的组合方案——那种方案
    # 看不出每个项目为什么被牵进来，业务上也没法执行。扫到求解视界仍找不到可行
    # 日期的项目，直接不出方案。
    for project_id in _search_order(project_ids, original_deadlines):
        if monotonic() >= search_deadline:
            break
        adjustment = _first_verified_adjustment(
            db, scheduler, (project_id,), candidates, generate_kwargs, search_deadline,
            simulation_context,
        )
        if adjustment:
            results.append(_format_adjustment(
                adjustment, original_deadlines, project_labels or {}, priorities,
            ))
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


def _any_adjustment_can_help(db, scheduler, project_ids, candidates, generate_kwargs, simulation_context=None) -> bool:
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
    if simulation_context is None:
        return _probe_deadlines(db, scheduler, extreme, generate_kwargs) != INFEASIBLE
    return _probe_deadlines(db, scheduler, extreme, generate_kwargs, simulation_context) != INFEASIBLE


def _search_order(project_ids: list[int], original_deadlines: dict[int, datetime]) -> list[int]:
    """按结题日从早到晚试。

    卡住排程的通常是结题日最早的那个项目——它的任务被顶到期限之外，别的项目
    延多久都腾不出它需要的位置。此前按项目号顺序试，真正该延的项目排在后面时，
    要先在前面的项目上白试上百次。
    """
    return sorted(project_ids, key=lambda project_id: (original_deadlines[project_id], project_id))


def _first_verified_adjustment(
    db, scheduler, selected, candidates, generate_kwargs, search_deadline,
    simulation_context: SimulationContext | None = None,
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
        if simulation_context is None:
            status = _probe_deadlines(db, scheduler, adjustment, generate_kwargs)
        else:
            status = _probe_deadlines(db, scheduler, adjustment, generate_kwargs, simulation_context)
        if status == FEASIBLE:
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


def _probe_deadlines(
    db, scheduler, deadlines: dict[int, datetime], generate_kwargs: dict,
    simulation_context: SimulationContext | None = None,
) -> str:
    """在给定的一组结题日下试解一次，返回三态判定。

    必须把"求解器证明了排不下"和"5 秒内没算出来"分开：后者什么都没证明。
    最宽松那次探测放开了全部结题日上界，模型反而更难收敛，实测就会超时返回
    UNKNOWN——若把它当成排不下，本来存在的调整方案会被整批丢掉。
    """
    if simulation_context is not None:
        generate_kwargs = dict(generate_kwargs)
        generate_kwargs["simulation_context"] = simulation_context.fork(deadlines)
    result = scheduler.generate(
        **generate_kwargs, commit=False, emit_advance_notifications=False,
        include_failure_diagnostics=False, solver_time_limit=5.0,
        feasibility_only=True, project_end_date_overrides=deadlines,
    )
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
    """按延期天数从小到大排。

    天数相同时，优先推低优先级的项目——同样延 2 天，动三级项目比动一级项目
    代价小。最后按项目号兜底，保证同样的输入永远给出同样的顺序。
    """
    return sorted(results, key=lambda item: (
        sum(change["delay_days"] for change in item["changes"]),
        -max(change["project_priority"] for change in item["changes"]),
        tuple(item["projects"]),
    ))[:MAX_RECOMMENDATIONS]

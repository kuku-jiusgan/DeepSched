from __future__ import annotations

from datetime import datetime, timedelta
from time import monotonic
import logging

from app.models import Project
from app.services.schedule_snapshot import SimulationContext

_logger = logging.getLogger(__name__)

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
            db, scheduler, project_id, candidates, generate_kwargs, search_deadline,
            simulation_context,
        )
        if adjustment and _survives_real_scheduling(
            db, adjustment, generate_kwargs.get("current_project_id"),
        ):
            results.append(_format_adjustment(
                adjustment, original_deadlines, project_labels or {}, priorities,
            ))
    return _sort_results(results)


def _survives_real_scheduling(db, adjustment: dict, current_project_id: int | None) -> bool:
    """把候选方案放到真实的「保存并排程」入口上再验一次。

    搜索阶段是拿失败当时抓下的一份 generate_kwargs 直接重放求解，跳过了真实入口
    在求解前要做的准备（删掉可移动任务的时间槽、重置任务状态），也不认「需要移动
    别的项目」这种独立结果。两者并不等价：实测同一个结题日，重放成功、真实入口
    失败，于是四套「求解器已验证」的方案里有三套照做之后仍然排不下——用户按提示
    改完结题日，回来得到的还是同一句失败。

    代价是每套候选多跑一次完整排程（约 1.5 秒），换的是这个标签名副其实。必须
    真的改 Project.end_date 再跑：真实入口读的就是它，用覆盖参数验不出同一条路。
    全程在 savepoint 里，跑完回滚。
    """
    if current_project_id is None:
        return True
    from app.services.project_plan_apply_service import apply_project_plan
    from app.services.schedule_deadline_recommendation_job_service import (
        suppress_recommendation_jobs,
    )

    savepoint = db.begin_nested()
    try:
        for project_id, deadline in adjustment.items():
            project = db.query(Project).filter(Project.id == project_id).first()
            if project is not None:
                project.end_date = deadline
        db.flush()
        # 复核用的是真实入口，而它失败时会顺手再排一个方案搜索作业——不挡住的话
        # 复核自己又触发一轮搜索，层层套下去。实测套了 170 秒。
        with suppress_recommendation_jobs():
            result = apply_project_plan(db, current_project_id)
        return getattr(result, "status", "error") != "error"
    except Exception:
        _logger.exception("方案复核失败 adjustment=%s", sorted(adjustment))
        return False
    finally:
        savepoint.rollback()


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
    db, scheduler, project_id: int, candidates, generate_kwargs, search_deadline,
    simulation_context: SimulationContext | None = None,
):
    """二分找这个项目最短要延几天，找不到返回 None。

    延后结题日只放宽任务的完工上界，是单调放松：某个日期可行，则更晚的日期必然
    可行。于是候选序列上"前面全不可行、后面全可行"，可以二分。

    逐天顺序试的代价全压在「其实没有解」的项目上——必须一路试到求解视界才能断言
    单独延它不行。实测一次搜索里三个这样的项目吃掉了 227 次试解，二分每个 7 次
    就能得到同样结论。

    5 秒没算出结论的（undetermined）当作"尚未证明可行"往右找。这样返回的日期
    仍然是求解器验证过可行的，只是真正的最小值那天恰好超时时会比它晚一点——
    逐天扫也有同样的问题，只是每次只跳一天。
    """
    dates = candidates.get(project_id) or []
    low, high = 0, len(dates) - 1
    found = None
    while low <= high:
        if monotonic() >= search_deadline:
            break
        middle = (low + high) // 2
        adjustment = {project_id: dates[middle]}
        if simulation_context is None:
            status = _probe_deadlines(db, scheduler, adjustment, generate_kwargs)
        else:
            status = _probe_deadlines(db, scheduler, adjustment, generate_kwargs, simulation_context)
        if status == FEASIBLE:
            found = middle
            high = middle - 1
        else:
            low = middle + 1
    return {project_id: dates[found]} if found is not None else None


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

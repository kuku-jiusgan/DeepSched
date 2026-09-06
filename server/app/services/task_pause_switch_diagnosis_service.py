"""暂停并切换失败的诊断。

暂停并切换只有一种失败：切换之后某个项目做不完，会超出与客户签的结题日期
（含不占时间轴、但计入完工测算的那部分后续任务）。所以这里不去猜"受什么约束
限制"，而是把这次切换原样再交给求解器跑一遍，只临时放开各项目的结题日——放开
之后排出来的那份计划，就是"如果允许延期，实际会做到哪一天"的答案。逐个项目跟
结题日一比，超出的就是原因，超出多少天就是要调整的天数，原因与方案一一对应。

此前这条路复用的是项目计划排程的失败诊断：文案按"当前项目 + 仪器工时缺口"的
启发式生成，而当前项目取的是源任务所在项目，真正被顶出结题日的项目根本不在这
句话里；调整方案则由后台作业拿「保存并开始排程」入口去验证，验的不是这次切换。
实测给出的是"缺口 0 / 受排程约束限制"加上"把别的项目延期"，原因说不清，方案也
对不上。
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.models import Project, Task, TimeSlot
from app.services.project_completion_projection_service import project_task_completions
from app.services.project_deadline_calendar_service import next_working_deadline
from app.services.schedule_queue_replan_support import load_working_options
from app.services.task_pause_switch_context_service import (
    PauseSwitchContext, build_pause_switch_context,
)


_logger = logging.getLogger(__name__)

# 复算就多这一次求解，值得给它比正式排程宽的时限。放开结题日等于抽掉了一大批
# 上界，搜索空间随之变大：实测同一道题 8 秒只能给出 UNKNOWN，60 秒能证到 OPTIMAL。
RELAXED_SOLVER_TIME_LIMIT = 30.0

FEASIBLE = "feasible"          # 放开结题日就排得下，可以逐项目算超期
INFEASIBLE = "infeasible"      # 求解器证明了放开也排不下
UNDETERMINED = "undetermined"  # 求解超时，什么也没证明


def diagnose_pause_switch_failure(
    db,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
    started_at: datetime,
    solver_message: str | None = None,
) -> dict:
    """Explain a failed pause-and-switch in terms of project deadlines."""
    context = build_pause_switch_context(db, source_slot, target_slot, started_at)
    parties = _switch_parties(db, context)
    status, completions = _relaxed_replan(db, context, source_slot, target_slot)
    if status == INFEASIBLE:
        return _conflict_failure(context, parties, solver_message)
    overruns = _overruns(db, completions) if status == FEASIBLE else []
    if overruns:
        return _overrun_failure(context, parties, overruns)
    if status == FEASIBLE:
        # 放开结题日就排得下、却没有任何项目超期，意味着那份排程原样也满足严格
        # 约束——严格那次本不该失败。多半是它自己超时了。
        _logger.error(
            "pause_switch_relaxed_replan_feasible_without_overrun source_task_id=%s "
            "target_task_id=%s closure_task_ids=%s",
            context.source_task_id, context.target_task_id, sorted(context.task_ids),
        )
    return _undetermined_failure(context, parties, solver_message)


def _relaxed_replan(
    db,
    context: PauseSwitchContext,
    source_slot: TimeSlot,
    target_slot: TimeSlot,
) -> tuple[str, dict[int, dict[int, datetime]] | None]:
    """放开全部结题日期再跑一遍这次切换，返回各项目下每个任务的预计完工时间。

    除了结题日期这一条，其余输入与正式那次一字不差——包括求解视界。放开结题日是
    纯粹的松弛，同一个视界下只可能把"排不下"变成"排得下"。视界一并放宽反而危险：
    实测把它往后推 30 天，模型大到 8 秒内证不出结论，求解器返回 UNKNOWN，而
    UNKNOWN 被当成"排不下"就会得出"改日期解决不了"这种与事实相反的结论。

    所以只有求解器明确证明 INFEASIBLE 才算"排不下"，超时一律算没结论。
    全程在 savepoint 里，跑完回滚，一个字节都不落库。
    """
    from app.services.task_pause_solver_service import (
        apply_switch_anchors, run_switch_replan, solver_horizon_end,
    )

    project_ids = _closure_project_ids(db, context.task_ids)
    horizon_end = solver_horizon_end(
        context.queue_end, context.remaining_duration_minutes,
    )
    savepoint = db.begin_nested()
    try:
        apply_switch_anchors(db, context, source_slot, target_slot)
        result = run_switch_replan(
            db,
            context,
            project_end_date_overrides={
                project_id: horizon_end for project_id in project_ids
            },
            solver_time_limit=RELAXED_SOLVER_TIME_LIMIT,
        )
        if result.get("status") != "ok":
            _logger.error(
                "pause_switch_relaxed_replan_failed source_task_id=%s target_task_id=%s "
                "closure_task_ids=%s solver_status=%s message=%s",
                context.source_task_id, context.target_task_id,
                sorted(context.task_ids), result.get("solver_status"),
                result.get("message"),
            )
            return (
                INFEASIBLE if result.get("solver_status") == "INFEASIBLE"
                else UNDETERMINED
            ), None
        db.flush()
        options = load_working_options(db, context.switch_time)
        projects = db.query(Project).filter(Project.id.in_(project_ids)).all()
        return FEASIBLE, {
            project.id: _task_completions(db, project, options)
            for project in projects
        }
    finally:
        savepoint.rollback()
        db.expire_all()


def _task_completions(db, project: Project, options: dict) -> dict[int, datetime]:
    """任务的预计完工时间：排出来的时间槽末端与完工推演，取两者较晚的一个。

    时间槽末端是求解器自己的答案，结题日期这条硬约束卡的就是它；完工推演还盖住
    了不占时间轴的那部分（未签批方案的下游），业务上认的是它。建议的结题日期必须
    同时容得下两者，否则用户照着改完，回来还是同一句失败。
    """
    completions = dict(project_task_completions(db, project, options))
    task_ids = [
        task_id for (task_id,) in db.query(Task.id).filter(
            Task.project_id == project.id,
        ).all()
    ]
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
    ).all()
    for slot in slots:
        known = completions.get(slot.task_id)
        completions[slot.task_id] = max(known, slot.plan_end) if known else slot.plan_end
    return completions


def _overruns(db, completions_by_project: dict[int, dict[int, datetime]]) -> list[dict]:
    rows = []
    projects = db.query(Project).filter(
        Project.id.in_(list(completions_by_project)),
    ).all()
    for project in projects:
        row = _project_overrun(db, project, completions_by_project[project.id])
        if row:
            rows.append(row)
    return sorted(rows, key=lambda row: (-row["delay_days"], row["project_id"]))


def _project_overrun(db, project: Project, completions: dict[int, datetime]) -> dict | None:
    if not project.end_date or not completions:
        return None
    blocking_id = max(completions, key=lambda task_id: (completions[task_id], task_id))
    completion = completions[blocking_id]
    if completion <= project.end_date:
        return None
    suggested = next_working_deadline(db, completion)
    task = db.query(Task).filter(Task.id == blocking_id).first()
    return {
        "project_id": project.id,
        "project_label": _project_label(project),
        "deadline": _format_day(project.end_date),
        "projected_completion": _format_moment(completion),
        "delay_days": (suggested.date() - project.end_date.date()).days,
        "blocking_task_name": task.name if task else "未知任务",
        "blocking_task_assignee": _assignee_name(task),
        "suggested_deadline": _format_day(suggested),
    }


def _overrun_failure(context: PauseSwitchContext, parties: dict, overruns: list[dict]) -> dict:
    detail = "、".join(
        f"项目【{row['project_label']}】延期 {row['delay_days']} 天" for row in overruns
    )
    message = f"暂停并切换失败：本次切换会导致{detail}。"
    return {
        "message": message,
        "pause_switch_failure": {
            "title": "暂停并切换失败",
            "kind": "project_deadline_overrun",
            "summary": f"本次切换会导致 {len(overruns)} 个项目超出结题日期",
            "switch_time": _format_moment(context.switch_time),
            **parties,
            "overruns": overruns,
        },
    }


def _conflict_failure(context: PauseSwitchContext, parties: dict, solver_message: str | None) -> dict:
    """求解器证明了放开结题日期也排不下。

    按业务口径暂停并切换只会因为项目超出结题日期而失败，所以走到这里就是缺陷。
    这话只在拿到 INFEASIBLE 这个证明时才敢说——说错了就是让人白改一遍日期。
    """
    message = "暂停并切换失败：放开全部项目结题日期后仍排不下，属于排程约束冲突，改日期解决不了。"
    return _failure(
        context, parties, message,
        kind="scheduling_conflict",
        summary="排程约束冲突，不是结题日期的问题",
        solver_message=solver_message,
    )


def _undetermined_failure(
    context: PauseSwitchContext, parties: dict, solver_message: str | None,
) -> dict:
    """复算没能在时限内给出结论。

    没有结论就如实说没有结论。把超时说成"排程约束冲突"或者随便挑个项目让人去
    延期，都是拿一个没验证过的判断冒充结论。
    """
    message = "暂停并切换失败：诊断未能在限定时间内算出结论，请稍后重试；若反复出现请联系管理员。"
    return _failure(
        context, parties, message,
        kind="undetermined",
        summary="未能判定失败原因",
        solver_message=solver_message,
    )


def _failure(
    context: PauseSwitchContext,
    parties: dict,
    message: str,
    *,
    kind: str,
    summary: str,
    solver_message: str | None,
) -> dict:
    return {
        "message": message,
        "pause_switch_failure": {
            "title": "暂停并切换失败",
            "kind": kind,
            "summary": summary,
            # 求解器的原话留在这里：界面上不显眼，但它是排查这类失败唯一的线索，
            # 换成一句好听的概括就等于把证据丢了。
            "solver_message": solver_message,
            "switch_time": _format_moment(context.switch_time),
            **parties,
            "overruns": [],
        },
    }


def _switch_parties(db, context: PauseSwitchContext) -> dict:
    tasks = {
        task.id: task for task in db.query(Task).filter(
            Task.id.in_([context.source_task_id, context.target_task_id]),
        ).all()
    }
    return {
        "source": _party(tasks.get(context.source_task_id)),
        "target": _party(tasks.get(context.target_task_id)),
    }


def _party(task: Task | None) -> dict:
    return {
        "task_name": task.name if task else "未知任务",
        "project_label": _project_label(task.project) if task and task.project else "未知项目",
        "assignee_name": _assignee_name(task),
    }


def _closure_project_ids(db, task_ids: set[int]) -> list[int]:
    return sorted({
        project_id for (project_id,) in db.query(Task.project_id).filter(
            Task.id.in_(task_ids),
        ).distinct().all()
    })


def _assignee_name(task: Task | None) -> str:
    assignee = getattr(task, "assignee", None) if task else None
    return (assignee.display_name or assignee.username) if assignee else "未指定"


def _project_label(project: Project) -> str:
    code = getattr(project, "code", None)
    return f"{code} · {project.name}" if code and code != project.name else project.name


def _format_day(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "未设置"


def _format_moment(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "未设置"

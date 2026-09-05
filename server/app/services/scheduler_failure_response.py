"""求解失败时的诊断与响应组装。

CP-SAT 判定 INFEASIBLE 只说明"排不下"，不说明为什么。这里把失败快照、
资源缺口诊断和交期建议作业组装成前端可读的错误响应。
"""

from __future__ import annotations

from app.services.schedule_deadline_recommendation_job_service import (
    create_deadline_recommendation_job,
)
from app.services.scheduler_data import (
    load_bridge_candidate_tasks,
    load_diagnostic_resource_tasks,
    load_task_children,
)
from app.services.scheduler_diagnostics import (
    log_solver_failure_snapshot,
    schedule_infeasibility_diagnostic,
)
from app.services.scheduler_helpers import build_compatibility, build_dependencies


def build_failure_response(
    db,
    *,
    solver,
    status,
    tasks,
    instruments,
    compat,
    constraints,
    task_deps,
    missing_pred_ends,
    fixed_slots,
    global_prefix_sum,
    instrument_prefix_sums,
    horizon_start,
    horizon_end,
    total_units,
    current_project_id,
    relaxed_project_end_task_ids,
    include_failure_diagnostics,
    replan_request,
    released_slot_intervals=None,
) -> dict:
    """把一次失败的求解翻译成带诊断信息的错误响应。

    进来的任务和固定时间槽都是求解用的值对象，诊断这条路暂时还需要实体：它要从任务反向拿项目的
    全量叶子任务、顺着父链上溯、读时间槽，这些在值对象上都没有展开。用值对象跑
    不会报错，但会静默退化——实测同一个场景，缺口从 74 小时变成 0，根因从"计划内
    仪器工时不足"变成笼统的"受排程约束限制"，等于给出了错误的诊断。

    所以在这里按 id 把它们重新取回实体。这是一处明知的临时妥协："求解不碰库"这句
    话在失败分支上还不成立；彻底解决要把诊断依赖的那批数据也装载成视图（项目全量
    叶子任务 + 父链 + 时间槽），那是一次独立的改造。
    """
    tasks = _rehydrate(db, tasks)
    fixed_slots = _rehydrate_slots(db, fixed_slots)
    if not include_failure_diagnostics:
        return {
            "status": "error",
            "message": "未找到可行排程",
            "solver_status": solver.StatusName(status),
        }
    log_solver_failure_snapshot(
        tasks,
        compat,
        task_deps,
        missing_pred_ends,
        fixed_slots,
        instrument_prefix_sums,
        horizon_start,
        total_units,
        status,
    )
    try:
        diagnostic_tasks = tasks + load_diagnostic_resource_tasks(
            db,
            {task.id for task in tasks},
            current_project_id=current_project_id,
        )
        # 其他项目的桥接任务和依赖边不在求解输入里，占用分析要单独补齐，
        # 否则"仪器任务 A—非仪器任务—仪器任务 B"中间那段占用会被漏掉。
        loaded_task_ids = {task.id for task in diagnostic_tasks}
        bridge_tasks = [
            task for task in load_bridge_candidate_tasks(
                db, {task.project_id for task in diagnostic_tasks},
            )
            if task.id not in loaded_task_ids
        ]
        diagnostic_compat = build_compatibility(
            diagnostic_tasks + bridge_tasks,
            instruments,
            constraints["capability_matching"].is_enabled,
        )
        diagnostic_deps = sorted(set(task_deps) | set(build_dependencies(
            diagnostic_tasks + bridge_tasks,
            load_task_children(db, diagnostic_tasks + bridge_tasks),
        )))
        diagnostic = schedule_infeasibility_diagnostic(
            diagnostic_tasks + bridge_tasks,
            diagnostic_deps,
            missing_pred_ends,
            diagnostic_compat,
            global_prefix_sum,
            instrument_prefix_sums,
            horizon_start,
            total_units,
            current_project_id=current_project_id,
            excluded_task_ids=relaxed_project_end_task_ids,
            released_slot_intervals=released_slot_intervals,
        )
        diagnostic_message = diagnostic["message"]
        current_deadline = next(
            (task.project.end_date for task in tasks
             if task.project_id == current_project_id and task.project.end_date),
            None,
        )
        if current_deadline:
            job = create_deadline_recommendation_job(
                current_project_id,
                [task.id for task in tasks],
                current_deadline,
                horizon_start,
                horizon_end,
                instrument_prefix_sums,
                diagnostic["schedule_failure"],
                replan_request,
            )
            if job:
                diagnostic["schedule_failure"]["recommendation_job"] = job
    except Exception as exc:
        diagnostic_message = f"排程诊断失败：{exc}"
    response = {
        "status": "error",
        "message": diagnostic_message,
    }
    if 'diagnostic' in locals() and isinstance(diagnostic, dict):
        response["schedule_failure"] = diagnostic.get("schedule_failure")
    return response


def _rehydrate(db, tasks):
    """把求解用的任务值对象按 id 换回 ORM 实体，保持原有顺序。

    传进来的已经是实体时原样返回——诊断相关的几个 loader 本来就交实体。
    """
    from sqlalchemy.orm import selectinload

    from app.models import Project, Task, TaskDependency

    if db is None or not tasks:
        return tasks
    if any(isinstance(task, Task) for task in tasks):
        return tasks
    ordered_ids = [task.id for task in tasks]
    by_id = {
        entity.id: entity
        for entity in db.query(Task).filter(Task.id.in_(ordered_ids)).options(
            # 换回实体只是第一步；诊断随后要顺着这些关联走，不预加载就是逐任务
            # 发 SQL——排程失败时的诊断会随项目任务数线性变慢。
            selectinload(Task.project).selectinload(Project.tasks).selectinload(Task.children),
            selectinload(Task.project).selectinload(Project.tasks).selectinload(Task.time_slots),
            selectinload(Task.parent).selectinload(Task.parent),
            selectinload(Task.assignee),
            selectinload(Task.time_slots),
            selectinload(Task.predecessors).joinedload(TaskDependency.predecessor),
            selectinload(Task.capability_requirements),
        ).all()
    }
    return [by_id[task_id] for task_id in ordered_ids if task_id in by_id]


def _rehydrate_slots(db, slots):
    """把固定时间槽换回实体。

    诊断要读 slot.task.project.code、slot.task.assignee.display_name，还要顺着
    slot.task.parent 上溯——值对象上只有 project_id / assignee_id，直接 AttributeError。
    """
    from sqlalchemy.orm import joinedload, selectinload

    from app.models import Task, TimeSlot

    if db is None or not slots:
        return slots
    if any(isinstance(slot, TimeSlot) for slot in slots):
        return slots
    ordered_ids = [slot.id for slot in slots]
    by_id = {
        entity.id: entity
        for entity in db.query(TimeSlot).filter(TimeSlot.id.in_(ordered_ids)).options(
            # 诊断要读 slot.task.project.code、slot.task.assignee.display_name，
            # 还要顺着 slot.task.parent 上溯取顶层任务名。
            joinedload(TimeSlot.task).selectinload(Task.project),
            joinedload(TimeSlot.task).selectinload(Task.assignee),
            joinedload(TimeSlot.task).selectinload(Task.parent).selectinload(Task.parent),
        ).all()
    }
    return [by_id[slot_id] for slot_id in ordered_ids if slot_id in by_id]

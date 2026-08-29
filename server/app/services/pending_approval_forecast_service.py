"""待方案签批工时在仪器时间轴上的预测铺排。

方案签批通过前，下游任务既不进排程也不落地时间槽——签批哪天通过没有依据，
把工时钉在某个位置等于凭空预留一段仪器时间。但这些活在项目结题前一定要做，
时间轴上完全看不见会让排程显得比真实情况乐观。

这里把它们从该仪器最后一个已排时间槽之后起算，按工作日历依次铺开，让人一眼
看出这些工时会占到哪一天。**每个项目单独一段，不合并**：不同项目的签批各自
独立，合成一段就看不出是谁的活、也看不出先后。
"""

from __future__ import annotations

from datetime import datetime

from app.models import Project, Task, TaskDependency, TimeSlot
from app.services.schedule_working_time_service import working_time_chunks
from app.services.task_progress_service import remaining_task_minutes


ACTIVE_SLOT_LIFECYCLE = "active"


def pending_approval_segments(db) -> list[dict]:
    """按仪器铺排的待签批工时段，供甘特图直接渲染。"""
    tasks = _unscheduled_downstream_tasks(db)
    if not tasks:
        return []
    anchors = _instrument_anchors(db, {
        instrument_id for _task, instrument_id in tasks
    })
    project_ends = _project_schedule_ends(db, {task.project_id for task, _i in tasks})
    segments = []
    for instrument_id, items in _group_by_instrument(tasks).items():
        cursor = anchors.get(instrument_id) or datetime.now()
        for task in items:
            # 起点不能早于本项目自己已排工作的结束：签批后的活接在前置工作
            # 之后，仪器空出来了也不能提前做。
            cursor = max(cursor, project_ends[task.project_id])
            hours = remaining_task_minutes(task) / 60
            if hours <= 0:
                continue
            # 按工作日切段：一整段画过去会盖住周末和夜间，而排程本身也是按
            # 工作日切成多个时间槽的，预测不切段就跟真实排程长得不一样。
            chunks = working_time_chunks(db, cursor, hours, instrument_id)
            if not chunks:
                continue
            for index, (chunk_start, chunk_end) in enumerate(chunks):
                segments.append({
                    "instrument_id": instrument_id,
                    "project_id": task.project_id,
                    "project_code": task.project.code if task.project else "",
                    "project_name": task.project.name if task.project else "",
                    "task_id": task.id,
                    "task_name": task.name,
                    "hours": round(hours, 2),
                    "segment_index": index,
                    "plan_start": chunk_start,
                    "plan_end": chunk_end,
                })
            cursor = chunks[-1][1]
    return segments


def _unscheduled_downstream_tasks(db) -> list[tuple[Task, int]]:
    """未签批节点解锁的、还没有时间槽的仪器任务，连同它要用的仪器。"""
    gates = db.query(Task).filter(
        Task.is_external_gate.is_(True),
        Task.gate_status != "approved",
    ).all()
    if not gates:
        return []
    dependencies = db.query(TaskDependency).filter(
        TaskDependency.predecessor_id.in_([gate.id for gate in gates]),
    ).all()
    task_ids = {dependency.task_id for dependency in dependencies}
    if not task_ids:
        return []
    scheduled = _scheduled_task_ids(db, task_ids)
    # 只对已经进入排程的项目做预测。一个连方法开发都还没排的项目，谈不上
    # "签批后接着做"，把它的方法验证画到时间轴上会挤在别人前面、误导判断。
    planned_projects = _projects_with_active_slots(db)
    result = []
    for task in db.query(Task).filter(Task.id.in_(task_ids)).all():
        if task.id in scheduled or not task.requires_instrument:
            continue
        if task.project_id not in planned_projects:
            continue
        for instrument_id in task.instrument_ids or []:
            result.append((task, int(instrument_id)))
    return result


def _projects_with_active_slots(db) -> set[int]:
    rows = db.query(Task.project_id).join(TimeSlot, TimeSlot.task_id == Task.id).filter(
        TimeSlot.lifecycle_status == ACTIVE_SLOT_LIFECYCLE,
    ).distinct().all()
    return {row[0] for row in rows}


def _project_schedule_ends(db, project_ids: set[int]) -> dict[int, datetime]:
    """各项目已排工作的结束时刻；没有已排工作的按当前时刻。"""
    now = datetime.now()
    ends = {project_id: now for project_id in project_ids}
    if not project_ids:
        return ends
    rows = db.query(Task.project_id, TimeSlot.plan_end).join(
        TimeSlot, TimeSlot.task_id == Task.id,
    ).filter(
        Task.project_id.in_(project_ids),
        TimeSlot.lifecycle_status == ACTIVE_SLOT_LIFECYCLE,
        TimeSlot.plan_end.isnot(None),
    ).all()
    for project_id, plan_end in rows:
        if plan_end > ends[project_id]:
            ends[project_id] = plan_end
    return ends


def _group_by_instrument(tasks: list[tuple[Task, int]]) -> dict[int, list[Task]]:
    grouped: dict[int, list[Task]] = {}
    for task, instrument_id in tasks:
        grouped.setdefault(instrument_id, []).append(task)
    for items in grouped.values():
        # 结题日早的排前面，与排程的优先取向一致；同日按项目号稳定排序。
        items.sort(key=lambda task: (
            task.project.end_date or datetime.max,
            task.project.code or "",
            task.id,
        ))
    return grouped


def _instrument_anchors(db, instrument_ids: set[int]) -> dict[int, datetime]:
    """各仪器最后一个已排时间槽的结束时刻，早于当前时刻的从当前时刻起算。"""
    if not instrument_ids:
        return {}
    now = datetime.now()
    rows = db.query(TimeSlot.instrument_id, TimeSlot.plan_end).filter(
        TimeSlot.instrument_id.in_(instrument_ids),
        TimeSlot.lifecycle_status == ACTIVE_SLOT_LIFECYCLE,
        TimeSlot.plan_end.isnot(None),
    ).all()
    anchors: dict[int, datetime] = {}
    for instrument_id, plan_end in rows:
        if instrument_id not in anchors or plan_end > anchors[instrument_id]:
            anchors[instrument_id] = plan_end
    return {
        instrument_id: max(plan_end, now)
        for instrument_id, plan_end in anchors.items()
    }


def _scheduled_task_ids(db, task_ids: set[int]) -> set[int]:
    rows = db.query(TimeSlot.task_id).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == ACTIVE_SLOT_LIFECYCLE,
    ).distinct().all()
    return {row[0] for row in rows}

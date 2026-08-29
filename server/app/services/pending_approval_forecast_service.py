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
from app.services.schedule_working_time_service import advance_working_hours
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
    segments = []
    for instrument_id, items in _group_by_instrument(tasks).items():
        cursor = anchors.get(instrument_id) or datetime.now()
        for task in items:
            hours = remaining_task_minutes(task) / 60
            if hours <= 0:
                continue
            end = advance_working_hours(db, cursor, hours, instrument_id)
            segments.append({
                "instrument_id": instrument_id,
                "project_id": task.project_id,
                "project_code": task.project.code if task.project else "",
                "project_name": task.project.name if task.project else "",
                "task_id": task.id,
                "task_name": task.name,
                "hours": round(hours, 2),
                "plan_start": cursor,
                "plan_end": end,
            })
            cursor = end
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
    result = []
    for task in db.query(Task).filter(Task.id.in_(task_ids)).all():
        if task.id in scheduled or not task.requires_instrument:
            continue
        for instrument_id in task.instrument_ids or []:
            result.append((task, int(instrument_id)))
    return result


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

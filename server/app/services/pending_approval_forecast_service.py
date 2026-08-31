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
    """未签批节点解锁的、还没有时间槽的任务，连同它要占用的仪器。

    仪器任务直接按自己的可选仪器铺排。非仪器任务只有构成**桥接**时才铺——即
    它的前驱和后继都是仪器任务、三者同一负责人、且前后两个仪器任务用同一台
    仪器。这种任务虽然自己不动仪器，但样品还在机器里、别人插不进来，占用是
    实打实的。反之，链条末尾那种不占仪器的收尾工作不画在仪器甘特图上。
    """
    gates = db.query(Task).filter(
        Task.is_external_gate.is_(True),
        Task.gate_status != "approved",
    ).all()
    if not gates:
        return []
    successors, predecessors = _dependency_maps(db)
    # 沿整条下游链遍历。只看签批节点的直接后继会漏掉第二层及以后的任务——
    # 签批解锁的往往是"方法验证 → 报告撰写"这样一串，不是单个任务。
    task_ids = _downstream_chain_ids({gate.id for gate in gates}, successors)
    if not task_ids:
        return []
    scheduled = _scheduled_task_ids(db, task_ids)
    # 只对已经进入排程的项目做预测。一个连方法开发都还没排的项目，谈不上
    # "签批后接着做"，把它的方法验证画到时间轴上会挤在别人前面、误导判断。
    planned_projects = _projects_with_active_slots(db)
    tasks_by_id = {task.id: task for task in db.query(Task).filter(Task.id.in_(
        task_ids | _neighbour_ids(task_ids, successors, predecessors),
    )).all()}
    result = []
    for task_id in task_ids:
        task = tasks_by_id.get(task_id)
        if task is None or task.id in scheduled:
            continue
        if task.project_id not in planned_projects:
            continue
        if task.requires_instrument:
            for instrument_id in task.instrument_ids or []:
                result.append((task, int(instrument_id)))
            continue
        instrument_id = _preceding_instrument_id(task, tasks_by_id, predecessors)
        if instrument_id is not None:
            result.append((task, instrument_id))
    return result


def _dependency_maps(db) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    successors: dict[int, set[int]] = {}
    predecessors: dict[int, set[int]] = {}
    for dependency in db.query(TaskDependency).all():
        successors.setdefault(dependency.predecessor_id, set()).add(dependency.task_id)
        predecessors.setdefault(dependency.task_id, set()).add(dependency.predecessor_id)
    return successors, predecessors


def _downstream_chain_ids(gate_ids: set[int], successors: dict[int, set[int]]) -> set[int]:
    seen: set[int] = set()
    pending = [task_id for gate_id in gate_ids for task_id in successors.get(gate_id, set())]
    while pending:
        task_id = pending.pop()
        if task_id in seen:
            continue
        seen.add(task_id)
        pending.extend(successors.get(task_id, set()))
    return seen


def _neighbour_ids(
    task_ids: set[int],
    successors: dict[int, set[int]],
    predecessors: dict[int, set[int]],
) -> set[int]:
    neighbours: set[int] = set()
    for task_id in task_ids:
        neighbours |= successors.get(task_id, set())
        neighbours |= predecessors.get(task_id, set())
    return neighbours - task_ids


def _preceding_instrument_id(
    task: Task,
    tasks_by_id: dict[int, Task],
    predecessors: dict[int, set[int]],
) -> int | None:
    """非仪器任务紧邻的前一个仪器任务用的是哪台仪器。"""
    for previous_id in sorted(predecessors.get(task.id, set())):
        previous = tasks_by_id.get(previous_id)
        if previous is None or not previous.requires_instrument:
            continue
        if previous.assignee_id != task.assignee_id:
            continue
        ids = [int(i) for i in (previous.instrument_ids or [])]
        if ids:
            return min(ids)
    return None


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
    for instrument_id, items in grouped.items():
        # 结题日早的排前面，与排程的优先取向一致；同日按项目号稳定排序。
        # plan_order 保证同一项目内「方法验证 → 报告撰写」的先后不被打乱。
        items.sort(key=lambda task: (
            task.project.end_date or datetime.max,
            task.project.code or "",
            task.plan_order,
            task.id,
        ))
        grouped[instrument_id] = _bridging_only(items)
    return grouped


def _bridging_only(items: list[Task]) -> list[Task]:
    """去掉不构成桥接的非仪器任务。

    桥接按**仪器排队顺序跨项目**判定，不是在单个项目的任务链里判定：一个不占
    仪器的任务，只有当这台仪器上它后面还排着同一负责人的仪器任务时，才真的把
    仪器占住——样品还在机器里，别人插不进来。后面再没有仪器任务的（哪怕它在
    自己项目里还有后继），不画在仪器甘特图上。
    """
    kept: list[Task] = []
    for index, task in enumerate(items):
        if task.requires_instrument:
            kept.append(task)
            continue
        if any(
            later.requires_instrument and later.assignee_id == task.assignee_id
            for later in items[index + 1:]
        ):
            kept.append(task)
    return kept


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

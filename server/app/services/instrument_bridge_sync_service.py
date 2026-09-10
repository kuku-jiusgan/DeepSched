from __future__ import annotations


from sqlalchemy.orm import joinedload, selectinload
from app.models import InstrumentBridgeReservation, Task, TimeSlot


BRIDGE_SLOT_STATUSES = {
    "scheduled", "running", "blocked", "paused", "interrupted", "completed",
}


def _comparable_datetime(value):
    return value.replace(tzinfo=None) if value is not None and value.tzinfo else value


def rebuild_instrument_bridge_reservations(db, schedule_run_id: str | None = None) -> int:
    """Rebuild all derived bridge reservations from current active slots."""
    db.flush()
    db.query(InstrumentBridgeReservation).delete(synchronize_session=False)
    manual_slots = db.query(TimeSlot).join(Task).filter(
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
        Task.requires_instrument.is_(False),
        Task.requires_human.is_(True),
        Task.assignee_id.isnot(None),
        Task.status.notin_(["completed", "done"]),
    ).order_by(TimeSlot.plan_start, TimeSlot.id).all()
    created = 0
    # 逐槽判定都要走 _bridge_for_manual_task，而它内部按负责人做全库扫描。
    # 一次重建里同一个负责人会被重复扫几十遍——实测这一个函数占了整次排程
    # 三成的 SQL。缓存按负责人存一次，本次重建内复用。
    cache: dict = {_SOURCE_CACHE: _source_slots_by_task(
        db, {slot.task_id for slot in manual_slots},
    )}
    for slot in manual_slots:
        bridge = _bridge_for_manual_task(db, slot, cache)
        if bridge is None:
            continue
        previous, following = bridge
        db.add(InstrumentBridgeReservation(
            schedule_run_id=schedule_run_id or slot.schedule_run_id,
            task_id=slot.task_id,
            instrument_id=previous.instrument_id,
            previous_task_id=previous.task_id,
            following_task_id=following.task_id,
            plan_start=slot.plan_start,
            plan_end=slot.plan_end,
        ))
        created += 1
    db.flush()
    return created


def valid_bridge_reservations(db, query) -> list[InstrumentBridgeReservation]:
    # 逐条判定都要走一遍 _bridge_for_manual_task，而它内部按负责人全库扫描。
    # 一次请求里共用同一份缓存，同一负责人只扫一次。
    cache: dict = {}
    return [
        reservation for reservation in query.all()
        if _is_current(db, reservation, cache)
    ]


def active_bridge_reservation_views(db, start_date=None, end_date=None) -> list[dict]:
    """Derive bridge rows for active slots when persisted rows predate the sync hook."""
    query = db.query(TimeSlot).join(Task).options(
        joinedload(TimeSlot.task).joinedload(Task.project),
        joinedload(TimeSlot.task).joinedload(Task.assignee),
        joinedload(TimeSlot.task).selectinload(Task.execution_segments),
    ).filter(
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
        Task.requires_instrument.is_(False),
        Task.requires_human.is_(True),
        Task.assignee_id.isnot(None),
        Task.status.notin_(["completed", "done"]),
    )
    if start_date is not None:
        query = query.filter(TimeSlot.plan_end > start_date)
    if end_date is not None:
        query = query.filter(TimeSlot.plan_start < end_date)

    result = []
    cache: dict = {}
    for slot in query.order_by(TimeSlot.plan_start, TimeSlot.id).all():
        bridge = _bridge_for_manual_task(db, slot, cache)
        if bridge is None:
            continue
        previous, following = bridge
        actual_start, actual_end = _task_actual_window(slot.task)
        result.append({
            "id": -slot.id,
            "schedule_run_id": slot.schedule_run_id,
            "task_id": slot.task_id,
            "instrument_id": previous.instrument_id,
            "previous_task_id": previous.task_id,
            "following_task_id": following.task_id,
            "plan_start": slot.plan_start,
            "plan_end": slot.plan_end,
            "actual_start": actual_start,
            "actual_end": actual_end,
            "task": slot.task,
            "kind": "human_bridge_reservation",
        })
    return result


def bridge_reservation_rows(db, start_date=None, end_date=None) -> list:
    query = db.query(InstrumentBridgeReservation).options(
        joinedload(InstrumentBridgeReservation.task).joinedload(Task.project),
        joinedload(InstrumentBridgeReservation.task).joinedload(Task.assignee),
        joinedload(InstrumentBridgeReservation.task).selectinload(Task.execution_segments),
    )
    if start_date is not None:
        query = query.filter(InstrumentBridgeReservation.plan_end > start_date)
    if end_date is not None:
        query = query.filter(InstrumentBridgeReservation.plan_start < end_date)
    reservations = valid_bridge_reservations(
        db, query.order_by(InstrumentBridgeReservation.plan_start),
    )
    persisted_keys = {
        (item.task_id, item.instrument_id, item.plan_start, item.plan_end)
        for item in reservations
    }
    reservations.extend(
        item for item in active_bridge_reservation_views(db, start_date, end_date)
        if (item["task_id"], item["instrument_id"], item["plan_start"], item["plan_end"]) not in persisted_keys
    )
    return [*reservations, *historical_bridge_reservations(db, start_date, end_date)]


def historical_bridge_reservations(db, start_date=None, end_date=None) -> list[dict]:
    """Build read-only bridge intervals from completed manual task execution windows."""
    query = db.query(TimeSlot).join(Task).options(
        # slot.task 和 slot.task.time_slots 在循环里都要用到。不预加载的话每条
        # 时间槽各触发一次查询——实测 8 个结果发了 145 条 SQL。
        joinedload(TimeSlot.task).selectinload(Task.time_slots),
    ).filter(
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(["completed", "done"]),
        Task.requires_instrument.is_(False),
        Task.requires_human.is_(True),
        Task.assignee_id.isnot(None),
        Task.status.in_(["completed", "done"]),
        TimeSlot.actual_start.isnot(None),
        TimeSlot.actual_end.isnot(None),
    )
    # 下面按"任务整体执行窗口"与请求区间是否重叠来过滤。任务窗口取的是它全部
    # 时间槽实际时间的 min/max，所以"窗口尾 > start"等价于"存在某个槽的实际结束
    # > start"，"窗口头 < end"同理——可以原样下推到 SQL，不必把全库已完成的人工
    # 时间槽都取回来再在 Python 里筛。
    if start_date is not None:
        query = query.filter(TimeSlot.task_id.in_(
            db.query(TimeSlot.task_id).filter(TimeSlot.actual_end > start_date)
        ))
    if end_date is not None:
        query = query.filter(TimeSlot.task_id.in_(
            db.query(TimeSlot.task_id).filter(TimeSlot.actual_start < end_date)
        ))
    slots = query.order_by(TimeSlot.actual_start, TimeSlot.id).all()
    result = []
    cache: dict = {}
    for slot in slots:
        bridge = _bridge_for_manual_task(db, slot, cache)
        if bridge is None:
            continue
        actual_start = min(item.actual_start for item in slot.task.time_slots if item.actual_start)
        actual_end = max(item.actual_end for item in slot.task.time_slots if item.actual_end)
        if start_date and _comparable_datetime(actual_end) <= _comparable_datetime(start_date):
            continue
        if end_date and _comparable_datetime(actual_start) >= _comparable_datetime(end_date):
            continue
        previous, following = bridge
        result.append({
            "id": -slot.id,
            "schedule_run_id": slot.schedule_run_id,
            "task_id": slot.task_id,
            "instrument_id": previous.instrument_id,
            "previous_task_id": previous.task_id,
            "following_task_id": following.task_id,
            "plan_start": actual_start,
            "plan_end": actual_end,
            "actual_start": actual_start,
            "actual_end": actual_end,
            "task": slot.task,
            "kind": "historical_human_bridge",
        })
    return result


def _task_actual_window(task: Task) -> tuple:
    segments = [segment for segment in task.execution_segments if segment.started_at]
    actual_start = min((segment.started_at for segment in segments), default=None)
    actual_end = (
        max((segment.ended_at for segment in segments if segment.ended_at), default=None)
        if task.status in {"completed", "done"}
        else None
    )
    return actual_start, actual_end


def stale_bridge_reservation_ids(
    db,
    schedule_run_id: str | None = None,
) -> list[int]:
    """Return derived bridge reservations that no longer match active task slots."""
    query = db.query(InstrumentBridgeReservation)
    if schedule_run_id is not None:
        query = query.filter(InstrumentBridgeReservation.schedule_run_id == schedule_run_id)
    cache: dict = {}
    return [
        reservation.id for reservation in query.all()
        if not _is_current(db, reservation, cache)
    ]


def invalidate_task_bridge_reservations(db, task_id: int) -> int:
    return db.query(InstrumentBridgeReservation).filter(
        (InstrumentBridgeReservation.task_id == task_id)
        | (InstrumentBridgeReservation.previous_task_id == task_id)
        | (InstrumentBridgeReservation.following_task_id == task_id)
    ).delete(synchronize_session=False)


_SOURCE_CACHE = "source"


def _source_slots_by_task(db, task_ids: set[int]) -> dict[int, list[TimeSlot]]:
    """一次取齐这批任务的人工时间槽，按任务分组。

    原先是逐个任务查——重建桥接时每条候选槽都会问一次自己那个任务的槽，
    实测一次排程里这一条就发了 39 条 SQL。
    """
    if not task_ids:
        return {}
    grouped: dict[int, list[TimeSlot]] = {task_id: [] for task_id in task_ids}
    rows = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
    ).all()
    for row in rows:
        grouped[row.task_id].append(row)
    return grouped


def _bridge_for_manual_task(db, slot: TimeSlot, cache: dict | None = None) -> tuple[TimeSlot, TimeSlot] | None:
    # 候选集只取决于负责人，与具体时间槽无关，但这里是逐槽调用的：一次甘特图
    # 请求里同一个人的那条全库扫描会被重复几十遍，实测 9 条桥接要 1.2 秒。
    # cache 按负责人存一次，同一次请求内复用。
    cache = cache if cache is not None else {}
    by_task = cache.setdefault(_SOURCE_CACHE, {})
    if slot.task_id not in by_task:
        by_task.update(_source_slots_by_task(db, {slot.task_id}))
    source_slots = by_task.get(slot.task_id) or []
    if not source_slots:
        return None
    source_start = min(item.plan_start for item in source_slots)
    source_end = max(item.plan_end for item in source_slots)
    assignee_id = slot.task.assignee_id
    by_assignee = cache.setdefault("candidates", {})
    if assignee_id not in by_assignee:
        by_assignee[assignee_id] = db.query(TimeSlot).join(Task).filter(
            TimeSlot.lifecycle_status == "active",
            TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
            Task.requires_human.is_(True),
            Task.requires_instrument.is_(True),
        ).order_by(TimeSlot.plan_end.desc(), TimeSlot.id.desc()).all()
    candidates = [item for item in by_assignee[assignee_id] if item.task_id != slot.task_id]
    candidates_by_instrument: dict[int, list[TimeSlot]] = {}
    for item in candidates:
        if item.instrument_id is None:
            continue
        candidates_by_instrument.setdefault(item.instrument_id, []).append(item)

    # 逐台仪器判断相邻任务。另一台仪器上的更早任务不能遮蔽目标仪器上真正的
    # 后续任务；但同一台仪器上插入其他负责人的任务仍会打断桥接。
    for instrument_id, instrument_candidates in sorted(candidates_by_instrument.items()):
        previous = max(
            (
                item for item in instrument_candidates
                if (item.actual_end or item.plan_end) <= source_start
            ),
            key=lambda item: (item.actual_end or item.plan_end, item.id),
            default=None,
        )
        following = min(
            (item for item in instrument_candidates if item.plan_start >= source_end),
            key=lambda item: (item.plan_start, item.id),
            default=None,
        )
        if (
            previous is not None
            and following is not None
            and previous.instrument_id == instrument_id
            and previous.task.assignee_id == assignee_id
            and following.task.assignee_id == assignee_id
            and previous.task.requires_instrument
            and following.task.requires_instrument
        ):
            return previous, following
    return None


def _is_current(db, reservation: InstrumentBridgeReservation, cache: dict | None = None) -> bool:
    source = db.query(TimeSlot).filter(
        TimeSlot.task_id == reservation.task_id,
        TimeSlot.instrument_id.is_(None),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status.in_(BRIDGE_SLOT_STATUSES),
        TimeSlot.plan_start == reservation.plan_start,
        TimeSlot.plan_end == reservation.plan_end,
    ).first()
    if source is None:
        return False
    bridge = _bridge_for_manual_task(db, source, cache)
    return bool(
        bridge
        and bridge[0].task_id == reservation.previous_task_id
        and bridge[1].task_id == reservation.following_task_id
        and bridge[0].instrument_id == reservation.instrument_id
    )

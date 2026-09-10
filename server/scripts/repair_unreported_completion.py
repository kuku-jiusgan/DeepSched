"""Replay one recorded completion and restore its incorrectly postponed slots."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal
from app.models import (
    AuditLog, Task, TaskDependency, TaskExecutionSegment, TimeSlot,
    ScheduleSlotChangeLog,
)
from app.repositories.task_delay_repository import has_reported_task_delay
from app.services.schedule_completion_service import complete_task_and_shift
from app.services.schedule_conflict_service import (
    ensure_no_dependency_conflicts, find_human_conflicts, find_instrument_conflicts,
)
from app.services.schedule_epoch_service import claim, current_epoch
from app.services.schedule_slot_change_log_service import record_slot_created, supersede_slot
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations
from app.services.instrument_status_service import refresh_instrument_statuses


REASON = "修复未申报延期完成导致的错误顺延"
COPY_TABLES = (
    "project", "task", "time_slot", "task_dependency", "task_execution_segment",
    "instrument", "instrument_fault", "maintenance_window", "schedule_rule",
    "sys_calendar", "task_night_run",
)


def row_data(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def active_slots(db, task_ids) -> list[TimeSlot]:
    return db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids), TimeSlot.lifecycle_status == "active",
    ).order_by(TimeSlot.task_id, TimeSlot.plan_start, TimeSlot.id).all()


def load_event(db, source_slot_id: int, event_at: datetime) -> dict:
    source = db.get(TimeSlot, source_slot_id)
    if source is None or source.actual_end is None or source.status != "completed":
        raise ValueError("源任务缺少已完成的实际执行记录")
    if not source.plan_end < source.actual_end <= event_at <= source.actual_end + timedelta(seconds=10):
        raise ValueError("顺延事件时间与源任务延期完成时间不一致")
    if has_reported_task_delay(db, source.task_id):
        raise ValueError("源任务已申报延期，不适用本次修复")
    request_log = db.query(AuditLog).filter(
        AuditLog.action == "HTTP POST",
        AuditLog.detail["path"].as_string()
        == f"/api/v1/schedules/timeslots/{source_slot_id}/complete",
        AuditLog.created_at >= source.actual_end,
        AuditLog.created_at <= event_at + timedelta(seconds=10),
        AuditLog.detail["status"].as_integer() == 200,
    ).one()
    changes = db.query(ScheduleSlotChangeLog).filter(
        ScheduleSlotChangeLog.created_at == event_at,
        ScheduleSlotChangeLog.change_type == "superseded",
        ScheduleSlotChangeLog.reason_type == "排程重排",
    ).order_by(ScheduleSlotChangeLog.id).all()
    if not changes:
        raise ValueError("该事件没有可恢复的原始时间槽记录")
    originals = [db.get(TimeSlot, change.slot_id) for change in changes]
    for old, change in zip(originals, changes):
        if old is None or old.lifecycle_status != "superseded" or old.superseded_at != event_at:
            raise ValueError("历史时间槽已发生其他变更，停止修复")
        if (old.plan_start, old.plan_end) != (change.before_start, change.before_end):
            raise ValueError("历史时间槽与审计记录不一致")
    task_ids = {old.task_id for old in originals}
    current = active_slots(db, task_ids)
    tasks = db.query(Task).filter(Task.id.in_(task_ids)).order_by(Task.id).all()
    validate_unexecuted(db, tasks, originals, current, event_at)
    return {
        "source": source, "request_log": request_log, "changes": changes,
        "originals": originals, "current": current, "tasks": tasks,
        "task_ids": task_ids, "event_at": event_at,
    }


def validate_unexecuted(db, tasks, originals, current, event_at) -> None:
    task_ids = {task.id for task in tasks}
    if {slot.task_id for slot in current} != task_ids:
        raise ValueError("受影响任务缺少现行计划")
    if any(task.status != "scheduled" or task.executed_minutes for task in tasks):
        raise ValueError("受影响任务已执行或状态变化，不能覆盖")
    if db.query(TaskExecutionSegment.id).filter(TaskExecutionSegment.task_id.in_(task_ids)).first():
        raise ValueError("受影响任务已有执行历史，不能覆盖")
    if any(slot.actual_start or slot.actual_end or slot.is_night_run for slot in originals + current):
        raise ValueError("时间槽含实际执行或夜跑登记，不能覆盖")
    if any(slot.created_at != event_at or slot.updated_at != event_at for slot in current):
        raise ValueError("现行计划在顺延后已发生其他调整，停止修复")
    for task in tasks:
        old_minutes = sum((s.plan_end - s.plan_start).total_seconds() for s in originals if s.task_id == task.id)
        new_minutes = sum((s.plan_end - s.plan_start).total_seconds() for s in current if s.task_id == task.id)
        if old_minutes != new_minutes:
            raise ValueError("原计划和现行计划工时不同，停止修复")


def replay_completion(db, event: dict) -> dict:
    """Execute the real completion service in an isolated copy of schedule data."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            for name in COPY_TABLES:
                table = Base.metadata.tables[name]
                rows = [dict(row) for row in db.execute(select(table)).mappings()]
                if rows:
                    connection.execute(table.insert(), rows)
            logs = db.query(AuditLog).filter(AuditLog.action.in_(
                ["task_delay_reported", "task_paused"],
            )).all()
            if logs:
                connection.execute(AuditLog.__table__.insert(), [row_data(log) for log in logs])
        with Session(engine) as replay:
            prepare_replay(replay, event)
            source = replay.get(TimeSlot, event["source"].id)
            before = other_slot_state(replay, source.task_id)
            result = complete_task_and_shift(
                replay, source.task_id, actual_end_time=event["source"].actual_end,
                completed_slot_id=source.id, release_instrument=True,
            )
            replay.flush()
            if result.get("status") != "ok" or any(
                result.get(key) != 0 for key in ("moved_tasks", "delayed_slots", "delay_affected_tasks")
            ):
                raise ValueError("历史回放未通过：完成操作仍然触发排程调整")
            if other_slot_state(replay, source.task_id) != before:
                raise ValueError("历史回放未通过：其他任务时间槽发生变化")
            if source.actual_end != event["source"].actual_end or source.status != "completed":
                raise ValueError("历史回放未通过：实际完成记录不一致")
            return result
    finally:
        engine.dispose()


def prepare_replay(db, event) -> None:
    for row in event["current"]:
        slot = db.get(TimeSlot, row.id)
        slot.lifecycle_status, slot.status = "superseded", "cancelled"
    for row, change in zip(event["originals"], event["changes"]):
        slot = db.get(TimeSlot, row.id)
        slot.lifecycle_status, slot.status = "active", change.before_status
        slot.superseded_at = slot.superseded_reason = slot.superseded_by_slot_id = None
        slot.superseded_by = None
    source = db.get(TimeSlot, event["source"].id)
    if len(active_slots(db, {source.task_id})) != 1:
        raise ValueError("本回放入口要求源任务只有一个现行执行时间槽")
    segment = db.query(TaskExecutionSegment).filter(
        TaskExecutionSegment.task_id == source.task_id,
        TaskExecutionSegment.slot_id == source.id,
        TaskExecutionSegment.ended_at == source.actual_end,
    ).one()
    source.actual_end = None
    source.status = source.task.status = "running"
    segment.ended_at = segment.end_reason = None
    db.flush()
    db.expire_all()


def other_slot_state(db, source_task_id) -> list[dict]:
    return [row_data(slot) for slot in db.query(TimeSlot).filter(
        TimeSlot.task_id != source_task_id,
    ).order_by(TimeSlot.id).all()]


def check_conflicts(db, task_ids) -> None:
    for find in (find_instrument_conflicts, find_human_conflicts):
        conflicts = [item for item in find(db) if task_ids.intersection(
            {item["first_task_id"], item["second_task_id"]},
        )]
        if conflicts:
            raise ValueError(f"恢复计划产生资源冲突：{conflicts}")
    pairs = db.query(TaskDependency.task_id, TaskDependency.predecessor_id).filter(
        TaskDependency.task_id.in_(task_ids) | TaskDependency.predecessor_id.in_(task_ids),
    ).all()
    ensure_no_dependency_conflicts(db, pairs)


def restore_slots(db, event) -> list[TimeSlot]:
    for slot in event["current"]:
        supersede_slot(db, slot, REASON)
    restored = []
    for original, change in zip(event["originals"], event["changes"]):
        slot = TimeSlot(
            schedule_run_id=original.schedule_run_id, task_id=original.task_id,
            instrument_id=original.instrument_id, plan_start=original.plan_start,
            plan_end=original.plan_end, tier=original.tier,
            status=change.before_status, lifecycle_status="active",
        )
        db.add(slot)
        db.flush()
        record_slot_created(db, slot, REASON)
        restored.append(slot)
    db.flush()
    check_conflicts(db, event["task_ids"])
    rebuild_instrument_bridge_reservations(db)
    refresh_instrument_statuses(db, {slot.instrument_id for slot in restored})
    return restored


def write_backup(event, replay_result, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"unreported-completion-{event['source'].id}-{datetime.now():%Y%m%d%H%M%S%f}.json"
    payload = {
        "source_slot": row_data(event["source"]),
        "source_task": row_data(event["source"].task),
        "source_execution": [row_data(s) for s in event["source"].task.execution_segments],
        "tasks": [row_data(t) for t in event["tasks"]],
        "original_slots": [row_data(s) for s in event["originals"]],
        "superseded_current_slots": [row_data(s) for s in event["current"]],
        "source_request_log": row_data(event["request_log"]),
        "event_changes": [row_data(c) for c in event["changes"]],
        "replay_result": replay_result,
    }
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
    path.chmod(0o600)
    return path


def repair(db, source_slot_id, event_at, apply, backup_dir) -> dict:
    if apply:
        claim(db, current_epoch(db))
    event = load_event(db, source_slot_id, event_at)
    if apply:
        db.query(Task).filter(Task.id.in_(
            event["task_ids"] | {event["source"].task_id},
        )).order_by(Task.id).with_for_update().all()
        db.expire_all()
        event = load_event(db, source_slot_id, event_at)
    replay_result = replay_completion(db, event)
    source_before = row_data(event["source"])
    backup_path = write_backup(event, replay_result, backup_dir) if apply else None
    summary = {
        "mode": "applied" if apply else "preview",
        "source": f"{event['source'].task.project.code} · {event['source'].task.name}",
        "replay_result": replay_result,
        "backup": str(backup_path) if backup_path else None,
        "repaired_tasks": [
            {"project": t.project.code, "task": t.name,
             "before": [(s.plan_start, s.plan_end) for s in event["current"] if s.task_id == t.id],
             "after": [(s.plan_start, s.plan_end) for s in event["originals"] if s.task_id == t.id]}
            for t in event["tasks"]
        ],
    }
    restored = restore_slots(db, event)
    if row_data(event["source"]) != source_before:
        raise ValueError("源任务实际完成记录被修改，停止修复")
    if apply:
        audit = AuditLog(
            user_name="system", action="unreported_completion_repaired",
            target_type="task", target_id=event["source"].task_id,
            detail=json.loads(json.dumps({
                **summary, "reason": REASON,
                "source_request_log_id": event["request_log"].id,
                "original_slot_ids": [s.id for s in event["originals"]],
                "superseded_slot_ids": [s.id for s in event["current"]],
                "restored_slot_ids": [s.id for s in restored],
            }, default=str)),
        )
        db.add(audit)
        db.flush()
        summary["audit_id"] = audit.id
        summary["restored_slot_ids"] = [s.id for s in restored]
        db.commit()
    else:
        db.rollback()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-slot-id", type=int, required=True)
    parser.add_argument("--event-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=Path("../.runtime/repairs"))
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            result = repair(db, args.source_slot_id, args.event_at, args.apply, args.backup_dir)
        except Exception:
            db.rollback()
            raise
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

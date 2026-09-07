"""Repair completed tasks whose execution segment was left open.

Run without arguments for a read-only preview. Use --apply only after every
candidate has an authoritative actual end on its linked time slot.
"""

from __future__ import annotations

import argparse

from app.core.database import SessionLocal
from app.models import Task, TaskExecutionSegment, TimeSlot
from app.services.audit_log_service import record_audit_log


def repair_completed_open_segments(db, apply: bool = False) -> list[dict]:
    segments = (
        db.query(TaskExecutionSegment)
        .join(Task, Task.id == TaskExecutionSegment.task_id)
        .filter(
            Task.status.in_(["done", "completed"]),
            TaskExecutionSegment.ended_at.is_(None),
        )
        .order_by(TaskExecutionSegment.task_id, TaskExecutionSegment.started_at)
        .all()
    )
    repairs = [_validated_repair(db, segment) for segment in segments]
    if not apply:
        return repairs
    for repair, segment in zip(repairs, segments):
        segment.ended_at = repair["ended_at"]
        segment.end_reason = "slot_completed"
        record_audit_log(
            db,
            "system",
            "execution_segment_repaired",
            "task",
            segment.task_id,
            {
                "project_code": repair["project_code"],
                "task_name": repair["task_name"],
                "segment_id": segment.id,
                "ended_at": repair["ended_at"].isoformat(),
                "source": "time_slot.actual_end",
            },
        )
    db.commit()
    return repairs


def _validated_repair(db, segment: TaskExecutionSegment) -> dict:
    slot = db.query(TimeSlot).filter(TimeSlot.id == segment.slot_id).first()
    task = segment.task
    project = task.project if task else None
    label = " · ".join(
        value for value in [project.code if project else None, task.name if task else None]
        if value
    )
    if slot is None or slot.actual_end is None:
        raise RuntimeError(f"【{label}】缺少明确的时间槽实际结束时间，不能自动修复")
    if slot.actual_end < segment.started_at:
        raise RuntimeError(f"【{label}】时间槽实际结束时间早于执行开始时间，不能自动修复")
    return {
        "project_code": project.code if project else "",
        "project_name": project.name if project else "",
        "task_name": task.name if task else "",
        "segment_id": segment.id,
        "started_at": segment.started_at,
        "ended_at": slot.actual_end,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        repairs = repair_completed_open_segments(db, apply=args.apply)
        mode = "已修复" if args.apply else "待修复"
        for repair in repairs:
            print(
                f"{mode}: {repair['project_code']} · {repair['project_name']} · "
                f"{repair['task_name']} {repair['started_at']} -> {repair['ended_at']}"
            )
        print(f"{mode}记录数: {len(repairs)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

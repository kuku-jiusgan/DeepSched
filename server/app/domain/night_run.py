from datetime import datetime

from app.domain.errors import DomainValidationError


def night_run_status(start: datetime, end: datetime, now: datetime) -> str:
    if end <= start:
        raise DomainValidationError("夜间运行结束时间必须晚于开始时间")
    if now >= end:
        return "completed"
    return "running" if now >= start else "scheduled"


def night_slot_status(slot, now: datetime) -> str:
    if slot.lifecycle_status != "active" or slot.status not in {"scheduled", "running", "completed"}:
        return slot.status
    return night_run_status(slot.plan_start, slot.plan_end, now)

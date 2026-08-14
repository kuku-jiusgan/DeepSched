from __future__ import annotations

from datetime import datetime

from app.models import AuditLog, Instrument, InstrumentFault
from app.services.instrument_fault_schedule_service import (
    InstrumentFaultScheduleConflict,
    fault_affected_tasks,
    shift_faulted_instrument_slots,
)


class InstrumentFaultInvalidError(Exception):
    pass


class InstrumentFaultConflictError(Exception):
    def __init__(self, message: str, impact: dict):
        super().__init__(message)
        self.impact = impact


def list_open_faults(db):
    faults = (
        db.query(InstrumentFault)
        .filter(InstrumentFault.status == "open")
        .order_by(InstrumentFault.reported_at.desc())
        .all()
    )
    for fault in faults:
        _attach_fault_impact(db, fault)
    return faults


def list_faults(db):
    faults = (
        db.query(InstrumentFault)
        .order_by(InstrumentFault.reported_at.desc())
        .all()
    )
    for fault in faults:
        _attach_fault_impact(db, fault)
    return faults


def report_fault(db, instrument_id: int, description: str, estimated_resolved_at, resolved_at):
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not instrument:
        return None

    now = datetime.now()
    if not resolved_at and estimated_resolved_at <= now:
        raise InstrumentFaultInvalidError("预计维修完成时间必须晚于当前时间")

    is_resolved = resolved_at is not None
    instrument.status = "idle" if is_resolved else "fault"
    fault = InstrumentFault(
        instrument_id=instrument_id,
        description=description,
        estimated_resolved_at=estimated_resolved_at,
        resolved_at=resolved_at,
        status="resolved" if is_resolved else "open",
    )
    db.add(fault)
    impact = {"shifted_slots": 0, "affected_tasks": 0, "notified_users": 0}
    if not is_resolved:
        try:
            impact = shift_faulted_instrument_slots(
                db,
                instrument,
                now,
                estimated_resolved_at,
            )
        except InstrumentFaultScheduleConflict as exc:
            fault.schedule_impact = exc.impact
            fault.affected_tasks = exc.impact.get("affected_task_details", [])
            db.commit()
            raise InstrumentFaultConflictError(str(exc), exc.impact)
    db.flush()
    _record_fault_impact(db, fault.id, impact)
    db.commit()
    db.refresh(fault)
    fault.schedule_impact = impact
    fault.affected_tasks = impact.get("affected_task_details", [])
    return fault


def _attach_fault_impact(db, fault: InstrumentFault) -> None:
    impact = _stored_fault_impact(db, fault.id)
    if impact is None:
        fault.affected_tasks = fault_affected_tasks(db, fault)
        return
    fault.schedule_impact = impact
    fault.affected_tasks = impact.get("affected_task_details", [])


def _stored_fault_impact(db, fault_id: int) -> dict | None:
    log = (
        db.query(AuditLog)
        .filter(
            AuditLog.target_type == "instrument_fault",
            AuditLog.target_id == fault_id,
            AuditLog.action.in_([
                "instrument_fault_rescheduled",
                "instrument_fault_history_rescheduled",
            ]),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .first()
    )
    if not log or not isinstance(log.detail, dict):
        return None
    impact = log.detail.get("impact", log.detail)
    return impact if isinstance(impact, dict) else None


def _record_fault_impact(db, fault_id: int, impact: dict) -> None:
    db.add(AuditLog(
        user_name="system",
        action="instrument_fault_rescheduled",
        target_type="instrument_fault",
        target_id=fault_id,
        detail={"impact": impact},
    ))


def resolve_fault(db, instrument_id: int, fault_id: int):
    fault = db.query(InstrumentFault).filter(
        InstrumentFault.id == fault_id,
        InstrumentFault.instrument_id == instrument_id,
    ).first()
    if not fault:
        return None

    resolved_at = datetime.now()
    fault.resolved_at = resolved_at
    fault.status = "resolved"
    instrument = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if instrument:
        instrument.status = "idle"
    db.flush()
    if fault.estimated_resolved_at and resolved_at > fault.estimated_resolved_at:
        if instrument:
            try:
                shift_faulted_instrument_slots(
                    db,
                    instrument,
                    fault.estimated_resolved_at,
                    resolved_at,
                )
            except InstrumentFaultScheduleConflict as exc:
                raise InstrumentFaultConflictError(str(exc), exc.impact) from exc
    db.commit()
    db.refresh(fault)
    return fault

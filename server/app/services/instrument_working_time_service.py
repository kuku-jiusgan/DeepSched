from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from app.models import Instrument
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import load_calendar_days, time_horizon, working_time_bounds


@dataclass(frozen=True)
class WorkingTimePolicy:
    day_start_minutes: int
    day_end_minutes: int
    include_weekends: bool
    include_holidays: bool


@dataclass(frozen=True)
class WorkingTimeContext:
    global_policy: WorkingTimePolicy
    instrument_policies: dict[int, WorkingTimePolicy]
    calendar_days: dict[date, dict]
    horizon_end: datetime

    def policy_for(self, instrument_id: int | None) -> WorkingTimePolicy:
        if instrument_id is None:
            return self.global_policy
        policy = self.instrument_policies.get(instrument_id)
        if policy is None:
            raise ValueError(f"仪器 {instrument_id} 缺少有效工作时段配置")
        return policy


def load_working_time_context(
    db,
    horizon_start: datetime,
    horizon_end: datetime | None = None,
    instruments: list[Instrument] | None = None,
) -> WorkingTimeContext:
    constraints = get_solver_constraints(db)
    rule = constraints["working_hours"]
    params = rule.params or {}
    day_start, day_end = working_time_bounds(params)
    include_weekends = bool(params.get("include_weekends", False))
    include_holidays = bool(params.get("include_holidays", False))
    if not rule.is_enabled:
        day_start, day_end = 0, 24 * 60
        include_weekends, include_holidays = True, True
    global_policy = WorkingTimePolicy(
        day_start, day_end, include_weekends, include_holidays,
    )
    rows = instruments if instruments is not None else db.query(Instrument).all()
    policies = {
        instrument.id: (
            global_policy if not rule.is_enabled else WorkingTimePolicy(
                _time_to_minutes(instrument.effective_work_start),
                _time_to_minutes(instrument.effective_work_end),
                include_weekends,
                include_holidays,
            )
        )
        for instrument in rows
    }
    if horizon_end is None:
        _, horizon_end, _ = time_horizon()
    return WorkingTimeContext(
        global_policy=global_policy,
        instrument_policies=policies,
        calendar_days=load_calendar_days(db, horizon_start, horizon_end),
        horizon_end=horizon_end,
    )


def serialize_instrument_policies(context: WorkingTimeContext) -> dict[str, dict[str, str]]:
    return {
        str(instrument_id): {
            "day_start": _format_minutes(policy.day_start_minutes),
            "day_end": _format_minutes(policy.day_end_minutes),
        }
        for instrument_id, policy in context.instrument_policies.items()
    }


def validate_instrument_working_time(start: str, end: str) -> None:
    start_minutes = _time_to_minutes(start)
    end_minutes = _time_to_minutes(end)
    if start_minutes % 30 or end_minutes % 30:
        raise ValueError("有效工作时段必须使用 30 分钟刻度")
    if start_minutes >= end_minutes:
        raise ValueError("有效工作时段开始时间必须早于结束时间")


def _time_to_minutes(value: str | time) -> int:
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("有效工作时段必须使用 HH:mm 格式")
    hours, minutes = (int(part) for part in parts)
    if hours < 0 or hours > 24 or minutes < 0 or minutes > 59 or (hours == 24 and minutes != 0):
        raise ValueError("有效工作时段不是有效时间")
    return hours * 60 + minutes


def _format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"

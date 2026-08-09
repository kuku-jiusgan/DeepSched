from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.models import AuditLog, SysCalendar, Task, TimeSlot


VALID_DAY_TYPES = {"workday", "weekend", "holiday", "compensate"}
TIMOR_YEAR_API_URL = "http://timor.tech/api/holiday/year/{year}/"


class CalendarInvalidError(ValueError):
    pass


def ensure_calendar_range(db, start_date: date, end_date: date, commit: bool = False) -> int:
    existing_dates = {
        value for value, in db.query(SysCalendar.date).filter(
            SysCalendar.date >= start_date,
            SysCalendar.date <= end_date,
        ).all()
    }
    created = 0
    current = start_date
    while current <= end_date:
        if current not in existing_dates:
            is_working_day = current.weekday() < 5
            db.add(SysCalendar(
                date=current,
                is_working_day=is_working_day,
                day_type="workday" if is_working_day else "weekend",
                source="default",
            ))
            created += 1
        current += timedelta(days=1)
    if created:
        db.flush()
    if commit:
        db.commit()
    return created


def ensure_calendar_year(db, year: int, commit: bool = False) -> int:
    return ensure_calendar_range(db, date(year, 1, 1), date(year, 12, 31), commit)


def list_calendar(db, year: int, month: int | None = None):
    start_date = date(year, month or 1, 1)
    if month == 12:
        end_date = date(year, 12, 31)
    elif month:
        end_date = date(year, month + 1, 1) - timedelta(days=1)
    else:
        end_date = date(year, 12, 31)
    ensure_calendar_range(db, start_date, end_date, commit=True)
    return db.query(SysCalendar).filter(
        SysCalendar.date >= start_date,
        SysCalendar.date <= end_date,
    ).order_by(SysCalendar.date).all()


def update_calendar_date(
    db,
    target_date: date,
    *,
    is_working_day: bool,
    day_type: str,
    holiday_name: str | None,
    operator_name: str,
    source: str = "manual",
) -> tuple[SysCalendar, dict]:
    if day_type not in VALID_DAY_TYPES:
        raise CalendarInvalidError("日期类型不合法")
    ensure_calendar_range(db, target_date, target_date)
    day = db.query(SysCalendar).filter(SysCalendar.date == target_date).one()
    before = _day_snapshot(day)
    day.is_working_day = is_working_day
    day.day_type = day_type
    day.holiday_name = holiday_name
    day.source = source
    day.updated_at = datetime.now()
    impact = _calendar_change_impact(db, target_date, is_working_day)
    if before["is_working_day"] != is_working_day:
        _mark_impacted_tasks_dirty(db, target_date)
    db.add(AuditLog(
        user_name=operator_name,
        action="calendar_day_updated",
        target_type="calendar",
        target_id=day.id,
        detail={"date": target_date.isoformat(), "before": before, "after": _day_snapshot(day), **impact},
    ))
    db.commit()
    db.refresh(day)
    return day, impact


def record_calendar_fill_audit(db, year: int, created: int, operator_name: str) -> None:
    db.add(AuditLog(
        user_name=operator_name,
        action="calendar_year_filled",
        target_type="calendar",
        target_id=None,
        detail={"year": year, "created_days": created, "source": "default"},
    ))
    db.commit()


def sync_calendar_holidays(db, year: int, operator_name: str) -> dict:
    import json
    import urllib.request

    url = TIMOR_YEAR_API_URL.format(year=year)
    try:
        request = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; DeepSched/1.0)",
        })
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        raise CalendarInvalidError("节假日服务暂时不可用") from exc
    holidays = payload.get("holiday", {}) if payload.get("code") == 0 else {}
    if not holidays:
        raise CalendarInvalidError(f"未找到{year}年节假日数据")

    created = ensure_calendar_year(db, year)
    updated = 0
    for date_text, info in holidays.items():
        target_date = date.fromisoformat(f"{year}-{date_text}")
        day = db.query(SysCalendar).filter(SysCalendar.date == target_date).one()
        is_holiday = bool(info.get("holiday", False)) if isinstance(info, dict) else bool(info)
        day.is_working_day = not is_holiday
        day.holiday_name = info.get("name") if is_holiday and isinstance(info, dict) else None
        day.day_type = "holiday" if is_holiday else "compensate"
        day.source = "sync"
        day.updated_at = datetime.now()
        updated += 1
    db.add(AuditLog(
        user_name=operator_name,
        action="calendar_holidays_synced",
        target_type="calendar",
        target_id=None,
        detail={"year": year, "created_days": created, "updated_days": updated, "source": url},
    ))
    db.commit()
    return {"detail": f"同步完成：补齐{created}天，更新{updated}天", "year": year}


def _calendar_change_impact(db, target_date: date, is_working_day: bool) -> dict:
    start = datetime.combine(target_date, time.min)
    end = start + timedelta(days=1)
    rows = db.query(Task.id, Task.project_id).join(TimeSlot).filter(
        TimeSlot.plan_end > start,
        TimeSlot.plan_start < end,
        TimeSlot.actual_start.is_(None),
        TimeSlot.status.in_(["scheduled", "blocked"]),
    ).distinct().all()
    return {
        "affected_task_count": len(rows),
        "affected_project_count": len({project_id for _, project_id in rows}),
        "needs_reschedule": bool(rows) and not is_working_day,
    }


def _mark_impacted_tasks_dirty(db, target_date: date) -> None:
    start = datetime.combine(target_date, time.min)
    end = start + timedelta(days=1)
    task_ids = db.query(TimeSlot.task_id).filter(
        TimeSlot.plan_end > start,
        TimeSlot.plan_start < end,
        TimeSlot.actual_start.is_(None),
        TimeSlot.status.in_(["scheduled", "blocked"]),
    ).distinct().all()
    ids = [task_id for task_id, in task_ids]
    if ids:
        db.query(Task).filter(Task.id.in_(ids)).update(
            {Task.schedule_dirty: True}, synchronize_session=False
        )


def _day_snapshot(day: SysCalendar) -> dict:
    return {
        "is_working_day": bool(day.is_working_day),
        "day_type": day.day_type,
        "holiday_name": day.holiday_name,
        "source": day.source,
    }

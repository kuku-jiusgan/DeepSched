from __future__ import annotations

import logging
import threading
import uuid
from datetime import date

from app.core.database import SessionLocal
from app.models import SysCalendar
from app.repositories.worker_lease_repository import acquire_worker_lease
from app.services.calendar_service import CalendarInvalidError, sync_calendar_holidays


CALENDAR_SYNC_INTERVAL_SECONDS = 24 * 60 * 60
CALENDAR_SYNC_LEASE_SECONDS = 5 * 60
CALENDAR_SYNC_LEASE_NAME = "calendar-holiday-sync"

_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def start_calendar_sync_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_sync_loop,
        name="calendar-holiday-sync-worker",
        daemon=True,
    )
    _worker_thread.start()


def stop_calendar_sync_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _sync_loop() -> None:
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            if acquire_worker_lease(
                db,
                CALENDAR_SYNC_LEASE_NAME,
                _worker_owner_id,
                CALENDAR_SYNC_LEASE_SECONDS,
            ):
                _sync_due_years(db, date.today())
        except Exception:
            db.rollback()
            _logger.exception("节假日后台同步检查失败")
        finally:
            db.close()
        _stop_event.wait(CALENDAR_SYNC_INTERVAL_SECONDS)


def _sync_due_years(db, today: date) -> list[int]:
    synced_years: list[int] = []
    for year in _years_due(today):
        if _has_synced_holidays(db, year):
            continue
        try:
            sync_calendar_holidays(db, year, "system")
            synced_years.append(year)
        except CalendarInvalidError as exc:
            db.rollback()
            _logger.warning("节假日同步失败 year=%s error=%s", year, exc)
    return synced_years


def _years_due(today: date) -> tuple[int, ...]:
    if (today.month, today.day) >= (12, 1):
        return today.year, today.year + 1
    return (today.year,)


def _has_synced_holidays(db, year: int) -> bool:
    return db.query(SysCalendar.id).filter(
        SysCalendar.date >= date(year, 1, 1),
        SysCalendar.date <= date(year, 12, 31),
        SysCalendar.source == "sync",
    ).first() is not None

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.repositories.worker_lease_repository import acquire_worker_lease
from app.services.instrument_utilization_service import calculate_instrument_utilization
from app.services.instrument_utilization_snapshot_service import save_utilization_snapshot


REFRESH_INTERVAL_SECONDS = 60
LEASE_NAME = "instrument-utilization-snapshot"
LEASE_SECONDS = 90
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def start_instrument_utilization_snapshot_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_refresh_loop, name="instrument-utilization-snapshot-worker", daemon=True)
    _worker_thread.start()


def stop_instrument_utilization_snapshot_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _refresh_loop() -> None:
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            if acquire_worker_lease(db, LEASE_NAME, _worker_owner_id, LEASE_SECONDS):
                settings = get_settings()
                end = datetime.now()
                start = end - timedelta(days=settings.STATS_WINDOW_DAYS)
                rows = calculate_instrument_utilization(db, start, end, settings.PERCENT_SCALE)
                save_utilization_snapshot(db, _snapshot_key(start, end), [row.model_dump(mode="json") for row in rows])
        except Exception:
            db.rollback()
            _logger.exception("仪器利用率快照后台刷新失败")
        finally:
            db.close()
        _stop_event.wait(REFRESH_INTERVAL_SECONDS)


def _snapshot_key(start: datetime, end: datetime) -> str:
    return f"{start.replace(second=0, microsecond=0).isoformat()}|{end.replace(second=0, microsecond=0).isoformat()}"

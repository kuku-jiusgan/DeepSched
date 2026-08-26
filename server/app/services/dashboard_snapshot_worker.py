from __future__ import annotations

import logging
import threading
import uuid

from app.core.database import SessionLocal
from app.repositories.worker_lease_repository import acquire_worker_lease


REFRESH_INTERVAL_SECONDS = 60
LEASE_NAME = "dashboard-stats-snapshot"
LEASE_SECONDS = 90

_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def start_dashboard_snapshot_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_refresh_loop,
        name="dashboard-stats-snapshot-worker",
        daemon=True,
    )
    _worker_thread.start()


def stop_dashboard_snapshot_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _refresh_loop() -> None:
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            if acquire_worker_lease(db, LEASE_NAME, _worker_owner_id, LEASE_SECONDS):
                from app.api.stats import dashboard

                dashboard(start_date=None, end_date=None, db=db)
        except Exception:
            db.rollback()
            _logger.exception("仪表盘统计快照后台刷新失败")
        finally:
            db.close()
        _stop_event.wait(REFRESH_INTERVAL_SECONDS)

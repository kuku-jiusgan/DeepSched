import logging
import threading
import uuid

from app.core.database import SessionLocal
from app.repositories.worker_lease_repository import acquire_worker_lease
from app.services.night_run_lifecycle_service import advance_night_runs


_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def start_night_run_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_advance_loop, name="night-run-worker", daemon=True)
    _worker_thread.start()


def stop_night_run_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _advance_loop() -> None:
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            if acquire_worker_lease(db, "night-run-lifecycle", _owner_id, 45):
                advance_night_runs(db)
                db.commit()
        except Exception:
            db.rollback()
            _logger.exception("夜间运行状态推进失败")
        finally:
            db.close()
        _stop_event.wait(30)

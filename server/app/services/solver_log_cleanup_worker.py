from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.services.scheduler_solver_trace_service import SOLVER_LOG_DIR

RETENTION_DAYS = 5
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_logger = logging.getLogger(__name__)


def cleanup_solver_logs(
    log_dir: Path = SOLVER_LOG_DIR,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now()) - timedelta(days=RETENTION_DAYS)
    if not log_dir.exists():
        return 0
    removed = 0
    for path in log_dir.iterdir():
        if not path.is_file():
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime)
        if modified_at < cutoff:
            path.unlink()
            removed += 1
    return removed


def start_solver_log_cleanup_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_cleanup_loop, name="solver-log-cleanup-worker", daemon=True,
    )
    _worker_thread.start()


def stop_solver_log_cleanup_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _cleanup_loop() -> None:
    while not _stop_event.is_set():
        try:
            removed = cleanup_solver_logs()
            if removed:
                _logger.info("solver_logs 清理完成，删除 %s 个超过 %s 天的日志", removed, RETENTION_DAYS)
        except Exception:
            _logger.exception("solver_logs 清理失败")
        _stop_event.wait(CHECK_INTERVAL_SECONDS)

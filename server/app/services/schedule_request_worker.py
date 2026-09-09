from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime

from app.core.database import SessionLocal
from app.models import ScheduleRunRequest, User
from app.repositories.worker_lease_repository import acquire_worker_lease
from app.services.schedule_request_service import (
    claim_next_schedule_request,
    recover_stale_schedule_requests,
    touch_schedule_request,
)
from app.services.schedule_run_lock_service import ScheduleBusyError


REFRESH_INTERVAL_SECONDS = 2
LEASE_NAME = "schedule-request-worker"
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 10
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def start_schedule_request_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_run_loop, name=LEASE_NAME, daemon=True)
    _worker_thread.start()


def stop_schedule_request_worker() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _run_loop() -> None:
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            if acquire_worker_lease(db, LEASE_NAME, _owner_id, LEASE_SECONDS):
                recover_stale_schedule_requests(db)
                _run_one(db)
        except Exception:
            db.rollback()
            _logger.exception("排程请求 Worker 执行失败")
        finally:
            db.close()
        _stop_event.wait(REFRESH_INTERVAL_SECONDS)


def _run_one(db) -> None:
    request = claim_next_schedule_request(db)
    if request is None:
        return
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(request.id, heartbeat_stop),
        name=f"schedule-request-heartbeat-{request.id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        result = _execute_request(db, request)
        if request.request_type == "approval_gate" and getattr(result, "schedule_status", None) == "queued":
            request.status = "queued"
            request.started_at = None
            request.heartbeat_at = None
            request.error_message = "排程计算正在进行中，等待下一次执行"
            db.commit()
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
            return
        result_status = getattr(result, "status", None)
        if request.request_type == "approval_gate":
            result_status = "applied" if getattr(result, "schedule_status", None) == "applied" else result_status
        if result_status not in {"applied", "no_changes", "queued"}:
            raise RuntimeError(getattr(result, "message", None) or "排程未完成")
        request.status = "succeeded"
        request.result = result.model_dump(mode="json") if hasattr(result, "model_dump") else {
            "status": result_status,
            "message": getattr(result, "schedule_message", None),
        }
        request.error_message = None
    except ScheduleBusyError:
        db.rollback()
        request = db.get(ScheduleRunRequest, request.id)
        request.status = "queued"
        request.started_at = None
        request.heartbeat_at = None
        request.error_message = "排程计算正在进行中，等待下一次执行"
        db.commit()
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        return
    except Exception as exc:
        db.rollback()
        request = db.get(ScheduleRunRequest, request.id)
        request.status = "failed"
        request.error_message = str(exc)
    request.finished_at = datetime.now()
    request.heartbeat_at = request.finished_at
    db.commit()
    heartbeat_stop.set()
    heartbeat_thread.join(timeout=1)


def _heartbeat_loop(request_id: str, stop_event: threading.Event) -> None:
    while not stop_event.wait(HEARTBEAT_SECONDS):
        heartbeat_db = SessionLocal()
        try:
            touch_schedule_request(heartbeat_db, request_id)
            acquire_worker_lease(heartbeat_db, LEASE_NAME, _owner_id, LEASE_SECONDS)
        except Exception:
            heartbeat_db.rollback()
            _logger.exception("排程请求心跳续租失败 request_id=%s", request_id)
        finally:
            heartbeat_db.close()


def _execute_request(db, request):
    touch_schedule_request(db, request.id)
    if request.request_type == "approval_gate":
        from app.services.approval_gate_service import approve_approval_gate
        user = db.get(User, request.payload["requested_by"])
        return approve_approval_gate(
            db, int(request.payload["gate_id"]), request.payload.get("note"), user,
        )
    if request.request_type == "save_and_schedule":
        from app.schemas.project_plan_draft_schemas import ProjectPlanSaveAndScheduleRequest
        from app.services.project_plan_draft_service import save_and_schedule_project_plan
        user = db.get(User, request.payload["requested_by"])
        data = ProjectPlanSaveAndScheduleRequest.model_validate(request.payload["data"])
        return save_and_schedule_project_plan(db, request.project_id, data, user)
    from app.services.project_plan_apply_service import apply_project_plan
    return apply_project_plan(db, request.project_id)

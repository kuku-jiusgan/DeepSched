from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from sqlalchemy import or_

from app.models import ScheduleEpoch, ScheduleRunRequest


ACTIVE_STATUSES = ("queued", "running")


def enqueue_schedule_request(
    db, project_id: int, user_id: int | None, request_type: str = "project_plan",
    priority: int = 50, payload: dict | None = None,
):
    dedupe_key = f"{request_type}:project:{project_id}"
    existing = db.query(ScheduleRunRequest).filter(
        ScheduleRunRequest.project_id == project_id,
        ScheduleRunRequest.request_type == request_type,
        ScheduleRunRequest.status.in_(ACTIVE_STATUSES),
    ).first()
    if existing:
        return existing
    epoch = db.get(ScheduleEpoch, 1)
    request = ScheduleRunRequest(
        id=str(uuid.uuid4()), project_id=project_id, request_type=request_type,
        requested_by=user_id, priority=priority, dedupe_key=dedupe_key,
        base_schedule_epoch=epoch.version if epoch else None,
        payload=payload or {"project_id": project_id},
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def get_schedule_request(db, request_id: str):
    return db.get(ScheduleRunRequest, request_id)


def cancel_schedule_request(db, request_id: str):
    request = db.get(ScheduleRunRequest, request_id)
    if request and request.status == "queued":
        request.status = "cancelled"
        request.finished_at = datetime.now()
        db.commit()
    return request


def recover_stale_schedule_requests(db, stale_seconds: int = 180) -> int:
    cutoff = datetime.now() - timedelta(seconds=stale_seconds)
    changed = db.query(ScheduleRunRequest).filter(
        ScheduleRunRequest.status == "running",
        or_(
            ScheduleRunRequest.heartbeat_at < cutoff,
            ScheduleRunRequest.heartbeat_at.is_(None),
        ),
    ).update({
        ScheduleRunRequest.status: "queued",
        ScheduleRunRequest.started_at: None,
        ScheduleRunRequest.heartbeat_at: None,
        ScheduleRunRequest.error_message: "排程 Worker 中断，已自动重新排队",
    }, synchronize_session=False)
    db.commit()
    return changed


def claim_next_schedule_request(db):
    """用条件 UPDATE 抢占队列项，避免多 Worker 同时执行同一请求。"""
    request = db.query(ScheduleRunRequest).filter(
        ScheduleRunRequest.status == "queued",
    ).order_by(
        ScheduleRunRequest.priority.asc(),
        ScheduleRunRequest.created_at.asc(),
    ).first()
    if request is None:
        return None
    now = datetime.now()
    claimed = db.query(ScheduleRunRequest).filter(
            ScheduleRunRequest.id == request.id,
            ScheduleRunRequest.status == "queued",
        ).update({
            ScheduleRunRequest.status: "running",
            ScheduleRunRequest.started_at: now,
            ScheduleRunRequest.heartbeat_at: now,
            ScheduleRunRequest.error_message: None,
        }, synchronize_session=False)
    if claimed != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(ScheduleRunRequest, request.id)


def touch_schedule_request(db, request_id: str) -> None:
    db.query(ScheduleRunRequest).filter(
        ScheduleRunRequest.id == request_id,
        ScheduleRunRequest.status == "running",
    ).update({ScheduleRunRequest.heartbeat_at: datetime.now()}, synchronize_session=False)
    db.commit()


def request_message(request: ScheduleRunRequest) -> str:
    return {
        "queued": "排程请求已进入队列",
        "running": "排程请求正在执行",
        "succeeded": "排程已完成",
        "failed": request.error_message or "排程失败",
        "cancelled": "排程请求已取消",
        "superseded": "排程请求已被更新请求替代",
    }.get(request.status, request.status)

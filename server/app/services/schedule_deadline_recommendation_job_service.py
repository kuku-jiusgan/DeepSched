from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime

from app.core.database import SessionLocal
from app.models import Project, ScheduleDeadlineRecommendationJob, Task
from app.repositories.worker_lease_repository import acquire_worker_lease
from app.services.project_plan_apply_helpers import plan_fingerprint
from app.services.scheduler_deadline_recommendation import enumerate_verified_date_adjustments


JOB_POLL_SECONDS = 1
JOB_LEASE_NAME = "schedule-deadline-recommendation"
JOB_LEASE_SECONDS = 35
_wake_event = threading.Event()
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None
_worker_owner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)


def enqueue_deadline_recommendation(
    db, project, tasks, original_deadline, horizon_start, horizon_end,
    instrument_prefix_sums, failure, generate_kwargs,
) -> dict | None:
    fingerprint = plan_fingerprint(db, project, tasks)
    conflict_ids = sorted({
        int(row["project_id"])
        for row in failure.get("occupancy", [])
        if row.get("project_id") and int(row["project_id"]) != project.id
    })
    scheduled_project_ids = {
        int(project_id) for project_id in (generate_kwargs.get("project_ids") or [])
        if int(project_id) != project.id
    }
    candidate_ids = sorted({project.id, *conflict_ids, *scheduled_project_ids})
    project_rows = db.query(Project).filter(Project.id.in_(candidate_ids)).all()
    job = ScheduleDeadlineRecommendationJob(
        id=str(uuid.uuid4()),
        project_id=project.id,
        plan_fingerprint=fingerprint,
        payload={
            "task_ids": [task.id for task in tasks],
            "original_deadline": original_deadline.isoformat(),
            "horizon_end": horizon_end.isoformat(),
            "project_ids": candidate_ids,
            "original_deadlines": {
                str(row.id): row.end_date.isoformat() for row in project_rows if row.end_date
            },
            "project_labels": {
                str(row.id): _project_label(row) for row in project_rows
            },
            "generate_kwargs": _serialize_generate_kwargs(generate_kwargs),
        },
    )
    db.add(job)
    db.flush()
    _wake_event.set()
    return {"id": job.id, "status": "pending", "poll_after_ms": 1500}


def create_deadline_recommendation_job(
    project_id: int,
    task_ids: list[int],
    original_deadline,
    horizon_start,
    horizon_end,
    instrument_prefix_sums,
    failure,
    generate_kwargs,
) -> dict | None:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        tasks = db.query(Task).filter(Task.id.in_(task_ids)).all()
        if not project or len(tasks) != len(set(task_ids)):
            return None
        job = enqueue_deadline_recommendation(
            db, project, tasks, original_deadline, horizon_start, horizon_end,
            instrument_prefix_sums, failure, generate_kwargs,
        )
        db.commit()
        return job
    except Exception:
        db.rollback()
        _logger.exception("创建排程调整方案后台任务失败 project_id=%s", project_id)
        return None
    finally:
        db.close()


def get_deadline_recommendation_job(db, project_id: int, job_id: str) -> dict | None:
    job = db.query(ScheduleDeadlineRecommendationJob).filter(
        ScheduleDeadlineRecommendationJob.id == job_id,
        ScheduleDeadlineRecommendationJob.project_id == project_id,
    ).first()
    if not job:
        return None
    recommendations = job.result if isinstance(job.result, list) else ([] if not job.result else [job.result])
    response = {
        "id": job.id,
        "status": job.status,
        "recommendations": recommendations,
        "recommendation": recommendations[0] if recommendations else None,
        "elapsed_seconds": _elapsed_seconds(job),
    }
    if job.status == "failed":
        response["message"] = "调整方案暂未生成，请调整计划后重新排程。"
    return response


def _elapsed_seconds(job) -> int | None:
    if not job.started_at:
        return None
    end = job.completed_at or datetime.now()
    return max(0, round((end - job.started_at).total_seconds()))


def start_deadline_recommendation_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop, name="schedule-deadline-recommendation-worker", daemon=True,
    )
    _worker_thread.start()


def stop_deadline_recommendation_worker() -> None:
    _stop_event.set()
    _wake_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=2)


def _worker_loop() -> None:
    while not _stop_event.is_set():
        _wake_event.wait(JOB_POLL_SECONDS)
        _wake_event.clear()
        db = SessionLocal()
        try:
            if acquire_worker_lease(db, JOB_LEASE_NAME, _worker_owner_id, JOB_LEASE_SECONDS):
                _process_next_job(db)
        except Exception:
            db.rollback()
            _logger.exception("排程调整方案后台计算失败")
        finally:
            db.close()


def _process_next_job(db) -> None:
    job = db.query(ScheduleDeadlineRecommendationJob).filter(
        ScheduleDeadlineRecommendationJob.status == "pending",
    ).order_by(ScheduleDeadlineRecommendationJob.created_at).first()
    if not job:
        return
    job.status = "running"
    job.started_at = datetime.now()
    db.commit()
    try:
        recommendation = _calculate_job(db, job)
        if job.status != "stale":
            job.status = "completed"
            job.result = recommendation
    except Exception:
        _logger.exception("排程调整方案验证求解失败 job_id=%s", job.id)
        job.status = "failed"
        job.error_message = "验证求解失败"
    job.completed_at = datetime.now()
    db.commit()


def _calculate_job(db, job) -> dict | None:
    payload = job.payload
    project = db.query(Project).filter(Project.id == job.project_id).one()
    tasks = db.query(Task).filter(Task.id.in_(payload["task_ids"])).all()
    if plan_fingerprint(db, project, tasks) != job.plan_fingerprint:
        job.status = "stale"
        return None
    from app.services.scheduler import SchedulerService

    generate_kwargs = _deserialize_generate_kwargs(payload["generate_kwargs"])
    generate_kwargs["current_project_id"] = project.id
    project_ids = [int(item) for item in payload.get("project_ids", [project.id])]
    deadlines = {
        int(project_id): datetime.fromisoformat(value)
        for project_id, value in payload.get("original_deadlines", {}).items()
    }
    labels = {
        int(project_id): value
        for project_id, value in payload.get("project_labels", {}).items()
    }
    return enumerate_verified_date_adjustments(
        db, SchedulerService(db, reuse_prepared_context=True), project_ids, deadlines,
        datetime.fromisoformat(payload["horizon_end"]), generate_kwargs, labels,
    )


def _project_label(project) -> str:
    return f"{project.code} · {project.name}" if project.code and project.code != project.name else project.name


def _serialize_generate_kwargs(values: dict) -> dict:
    return {
        key: _serialize_value(value)
        for key, value in values.items()
        if value is not None
    }


def _serialize_value(value):
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, set):
        return {"set": sorted(value)}
    if isinstance(value, tuple):
        return {"tuple": [_serialize_value(item) for item in value]}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    return value


def _deserialize_generate_kwargs(values: dict) -> dict:
    return {key: _deserialize_value(value) for key, value in values.items()}


def _deserialize_value(value):
    if isinstance(value, list):
        return [_deserialize_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "datetime" in value:
        return datetime.fromisoformat(value["datetime"])
    if "set" in value:
        return set(value["set"])
    if "tuple" in value:
        return tuple(_deserialize_value(item) for item in value["tuple"])
    return {key: _deserialize_value(item) for key, item in value.items()}

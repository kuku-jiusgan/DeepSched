from datetime import datetime, timedelta
from threading import Lock
from time import monotonic
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.database import get_db
from app.models import Instrument, TimeSlot, Task, Project
from app.schemas.schemas import DashboardData, UtilizationStats
from app.services.project_status_service import calculate_project_status
from app.services.lab_status_service import list_lab_status
from app.services.instrument_utilization_service import calculate_instrument_utilization
from app.api.users import auth_token, get_current_user
from app.schemas.project_progress_schemas import ProjectProgressList
from app.services.project_progress_service import list_project_progress
from app.services.lab_status_snapshot_service import load_lab_status_snapshot
from app.services.instrument_utilization_snapshot_service import load_utilization_snapshot, save_utilization_snapshot
from app.services.dashboard_snapshot_service import (
    load_dashboard_snapshot,
    load_latest_dashboard_snapshot,
    save_dashboard_snapshot,
)

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])
_DASHBOARD_CACHE_TTL_SECONDS = 60.0
_dashboard_cache_lock = Lock()
_dashboard_cache: dict[tuple[int, datetime, datetime], tuple[float, DashboardData]] = {}


@router.get("/dashboard", response_model=DashboardData)
def dashboard(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    db: Session = Depends(get_db),
    _force_refresh: bool = False,
):
    settings = get_settings()
    window_start, window_end = _stats_window(start_date, end_date, settings)
    snapshot_key = _dashboard_snapshot_key(window_start, window_end)
    snapshot_payload = load_dashboard_snapshot(db, snapshot_key)
    if snapshot_payload is None and not _force_refresh:
        snapshot_payload = load_latest_dashboard_snapshot(db, snapshot_key)
    if snapshot_payload:
        return DashboardData.model_validate(snapshot_payload)
    cache_enabled = db.bind.dialect.name != "sqlite"
    cache_key = (id(db.bind), window_start, window_end)
    if cache_enabled and not _force_refresh:
        with _dashboard_cache_lock:
            cached = _dashboard_cache.get(cache_key)
            if cached and monotonic() - cached[0] < _DASHBOARD_CACHE_TTL_SECONDS:
                return cached[1]

    active_inst = db.query(Instrument).filter(Instrument.availability_status == "available").count()
    total_inst = active_inst
    project_window_filter = (
        or_(Project.start_date.is_(None), Project.start_date < window_end),
        or_(Project.end_date.is_(None), Project.end_date > window_start),
    )
    projects = db.query(Project).filter(
        Project.project_kind == "project",
        *project_window_filter,
    ).options(
        selectinload(Project.tasks).selectinload(Task.time_slots)
    ).all()
    total_proj = len(projects)
    active_proj = sum(calculate_project_status(project) == "active" for project in projects)
    # 两种都算延期：已被明确标记为延期，或者最后一个时间槽都过去了还没做完。
    # 必须分开查再取并集：此前"已标记延期"写在 filter 里，而 having 又要求最后
    # 一个时间槽已结束，等于把标记那一支整个吞掉——明确标了延期但时间槽还没到期
    # 的任务永远不会计入。合并写进 having 也不行，MySQL 的 having 只能引用选出的
    # 列或聚合，delay_status 不在其中（sqlite 放行，所以用例发现不了）。
    now = datetime.now()
    unfinished = Task.status.notin_({"done", "completed"})
    overdue_ids = {
        row[0] for row in db.query(Task.id)
        .join(TimeSlot, TimeSlot.task_id == Task.id)
        .filter(unfinished)
        .group_by(Task.id)
        .having(func.max(TimeSlot.plan_end) < now)
        .all()
    }
    marked_ids = {
        row[0] for row in db.query(Task.id)
        .join(TimeSlot, TimeSlot.task_id == Task.id)
        .filter(unfinished, Task.delay_status == "delayed")
        .distinct()
        .all()
    }
    delayed = len(overdue_ids | marked_ids)

    utilization_rows = calculate_instrument_utilization(db, window_start, window_end, settings.PERCENT_SCALE)
    total_hours = sum(row.actual_run_hours for row in utilization_rows)
    total_available = sum(row.total_available_hours for row in utilization_rows)
    avg_util = round(total_hours / total_available * settings.PERCENT_SCALE, 1) if total_available > 0 else 0

    result = DashboardData(
        total_instruments=total_inst,
        active_instruments=active_inst,
        total_projects=total_proj,
        active_projects=active_proj,
        avg_utilization=avg_util,
        delayed_tasks=delayed,
        buffer_warnings=[],
        milestone_risks=[],
    )
    save_dashboard_snapshot(db, snapshot_key, result.model_dump(mode="json"))
    if cache_enabled:
        with _dashboard_cache_lock:
            _dashboard_cache[cache_key] = (monotonic(), result)
            if len(_dashboard_cache) > 128:
                oldest_key = min(_dashboard_cache, key=lambda key: _dashboard_cache[key][0])
                _dashboard_cache.pop(oldest_key, None)
    return result


def _dashboard_snapshot_key(window_start: datetime, window_end: datetime) -> str:
    normalized_start = window_start.replace(second=0, microsecond=0)
    normalized_end = window_end.replace(second=0, microsecond=0)
    return f"{normalized_start.isoformat()}|{normalized_end.isoformat()}"


@router.get("/utilization", response_model=List[UtilizationStats])
def utilization(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    window_start, window_end = _stats_window(start_date, end_date, settings)
    cache_key = _utilization_snapshot_key(window_start, window_end)
    snapshot = load_utilization_snapshot(db, cache_key)
    if snapshot is None:
        snapshot = load_utilization_snapshot(db, cache_key, allow_stale=True)
    if snapshot is not None:
        return snapshot
    rows = calculate_instrument_utilization(db, window_start, window_end, settings.PERCENT_SCALE)
    payload = [row.model_dump(mode="json") for row in rows]
    save_utilization_snapshot(db, cache_key, payload)
    return payload


def _utilization_snapshot_key(window_start: datetime, window_end: datetime) -> str:
    start = window_start.replace(second=0, microsecond=0)
    end = window_end.replace(second=0, microsecond=0)
    return f"{start.isoformat()}|{end.isoformat()}"


def _stats_window(start_date: datetime | None, end_date: datetime | None, settings) -> tuple[datetime, datetime]:
    now = datetime.now()
    window_start = start_date or (now - timedelta(days=settings.STATS_WINDOW_DAYS))
    window_end = end_date or now
    if window_end.date() > now.date():
        raise HTTPException(status_code=400, detail="筛选结束日期不能晚于当前日期")
    if window_end > now:
        window_end = now
    if window_start >= window_end:
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")
    return window_start, window_end


@router.get("/lab-status")
def lab_status(db: Session = Depends(get_db)):
    snapshot = load_lab_status_snapshot(db)
    if snapshot is not None:
        return snapshot
    return list_lab_status(db)


@router.get("/project-progress", response_model=ProjectProgressList)
def project_progress(
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    return list_project_progress(db, get_current_user(token, db))

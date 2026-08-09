from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.database import get_db
from app.models import Instrument, TimeSlot, Task, Project
from app.schemas.schemas import DashboardData, UtilizationStats
from app.services.project_status_service import calculate_project_status
from app.services.lab_status_service import list_lab_status
from app.services.task_delay_status_service import DELAYED_STATUS
from app.services.instrument_utilization_service import calculate_instrument_utilization
from app.api.users import auth_token, get_current_user
from app.schemas.project_progress_schemas import ProjectProgressList
from app.services.project_progress_service import list_project_progress

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("/dashboard", response_model=DashboardData)
def dashboard(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    window_start, window_end = _stats_window(start_date, end_date, settings)

    available_instruments = (
        db.query(Instrument)
        .filter(Instrument.availability_status == "available")
        .all()
    )
    total_inst = len(available_instruments)
    active_inst = db.query(Instrument).filter(Instrument.availability_status == "available").count()
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
    delayed = (
        db.query(Task.id)
        .join(TimeSlot, TimeSlot.task_id == Task.id)
        .filter(
            Task.delay_status == DELAYED_STATUS,
            TimeSlot.plan_end > window_start,
            TimeSlot.plan_start < window_end,
        )
        .distinct()
        .count()
    )

    utilization_rows = calculate_instrument_utilization(db, window_start, window_end, settings.PERCENT_SCALE)
    total_hours = sum(row.actual_run_hours for row in utilization_rows)
    total_available = sum(row.total_available_hours for row in utilization_rows)
    avg_util = round(total_hours / total_available * settings.PERCENT_SCALE, 1) if total_available > 0 else 0

    return DashboardData(
        total_instruments=total_inst,
        active_instruments=active_inst,
        total_projects=total_proj,
        active_projects=active_proj,
        avg_utilization=avg_util,
        delayed_tasks=delayed,
        buffer_warnings=[],
        milestone_risks=[],
    )


@router.get("/utilization", response_model=List[UtilizationStats])
def utilization(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    window_start, window_end = _stats_window(start_date, end_date, settings)
    return calculate_instrument_utilization(db, window_start, window_end, settings.PERCENT_SCALE)


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
    return list_lab_status(db)


@router.get("/project-progress", response_model=ProjectProgressList)
def project_progress(
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    return list_project_progress(db, get_current_user(token, db))

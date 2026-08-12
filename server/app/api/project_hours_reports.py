from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.users import auth_token, get_current_user
from app.core.database import get_db
from app.schemas.project_hours_report_schemas import ProjectHoursReportOut
from app.services.project_hours_report_service import build_project_hours_report, export_project_hours_report


router = APIRouter(prefix="/api/v1/reports/project-hours", tags=["reports"])


@router.get("", response_model=ProjectHoursReportOut)
def project_hours_report(
    start_date: date | None = Query(None), end_date: date | None = Query(None),
    keyword: str | None = Query(None, max_length=100), token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    return build_project_hours_report(db, get_current_user(token, db), start_date, end_date, keyword)


@router.get("/export")
def export_project_hours(
    start_date: date | None = Query(None), end_date: date | None = Query(None),
    keyword: str | None = Query(None, max_length=100), token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    report = build_project_hours_report(db, get_current_user(token, db), start_date, end_date, keyword)
    filename = f"project-hours-{date.today().isoformat()}.xlsx"
    return StreamingResponse(export_project_hours_report(report), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

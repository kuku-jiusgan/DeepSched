from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.access import require_management_user
from app.core.database import get_db
from app.services.calendar_service import list_calendar as list_persisted_calendar


router = APIRouter(
    prefix="/api/v1/calendar",
    tags=["calendar"],
    dependencies=[Depends(require_management_user)],
)


class CalendarDayOut(BaseModel):
    id: int
    date: date
    is_working_day: bool
    holiday_name: str | None = None
    day_type: str
    source: str
    affected_task_count: int = 0
    affected_project_count: int = 0
    needs_reschedule: bool = False
    model_config = {"from_attributes": True}


@router.get("", response_model=list[CalendarDayOut])
def list_calendar(year: int = Query(...), month: int | None = None, db: Session = Depends(get_db)):
    return list_persisted_calendar(db, year, month)


@router.get("/is-workday/{dt}")
def check_workday(dt: str, db: Session = Depends(get_db)):
    day = list_persisted_calendar(db, date.fromisoformat(dt).year)
    result = next(item for item in day if item.date == date.fromisoformat(dt))
    return CalendarDayOut.model_validate(result)


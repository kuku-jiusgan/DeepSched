from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.schemas import UtilizationStats
from app.services.instrument_utilization_report_service import (
    build_instrument_utilization_report,
    export_instrument_utilization_report,
)


router = APIRouter(prefix="/api/v1/reports/instrument-utilization", tags=["reports"])


@router.get("", response_model=list[UtilizationStats])
def instrument_utilization_report(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
):
    try:
        return build_instrument_utilization_report(
            db, start_date, end_date, get_settings().PERCENT_SCALE,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/export")
def export_instrument_utilization(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
):
    try:
        rows = build_instrument_utilization_report(
            db, start_date, end_date, get_settings().PERCENT_SCALE,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _excel_response(rows)


def _excel_response(rows: list[UtilizationStats]) -> StreamingResponse:
    filename = f"instrument-utilization-{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        export_instrument_utilization_report(rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

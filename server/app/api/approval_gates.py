from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.users import auth_token, get_current_user
from app.core.database import get_db
from app.schemas.approval_gate_schemas import (
    PendingApprovalSegmentOut,
    ApprovalGateActionOut,
    ApprovalGateApprove,
    ApprovalGateCreate,
    ApprovalGateListOut,
    ApprovalGateOut,
    ApprovalGateSubmit,
)
from app.services.pending_approval_forecast_service import pending_approval_segments
from app.services.approval_gate_service import (
    ApprovalGateInvalidError,
    ApprovalGateNotFoundError,
    ApprovalGatePermissionError,
    approve_approval_gate,
    confirm_approval_schedule,
    create_approval_gate,
    get_approval_gate,
    list_approval_gates,
    submit_approval_gate,
)


router = APIRouter(prefix="/api/v1", tags=["approval-gates"])


@router.post("/projects/{project_id}/approval-gates", response_model=ApprovalGateOut)
def create_gate(project_id: int, data: ApprovalGateCreate, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    return _handle(lambda: create_approval_gate(db, project_id, data, get_current_user(token, db)))


@router.get("/approval-gates", response_model=ApprovalGateListOut)
def list_gates(
    status: str | None = None,
    keyword: str | None = None,
    project_id: int | None = None,
    manager_id: int | None = None,
    risk: str | None = None,
    expected_from: datetime | None = None,
    expected_to: datetime | None = None,
    workspace_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    return list_approval_gates(db, get_current_user(token, db), status, keyword, project_id, manager_id, risk, expected_from, expected_to, page, page_size, workspace_only)


@router.get("/approval-gates/{gate_id}", response_model=ApprovalGateOut)
def get_gate(gate_id: int, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    return _handle(lambda: get_approval_gate(db, gate_id, get_current_user(token, db)))


@router.post("/approval-gates/{gate_id}/submit", response_model=ApprovalGateActionOut)
def submit_gate(gate_id: int, data: ApprovalGateSubmit, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    return _handle(lambda: submit_approval_gate(db, gate_id, data, get_current_user(token, db)))


@router.post("/approval-gates/{gate_id}/approve", response_model=ApprovalGateActionOut)
def approve_gate(gate_id: int, data: ApprovalGateApprove, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    return _handle(lambda: approve_approval_gate(db, gate_id, data.approval_note, get_current_user(token, db)))


@router.post("/approval-gates/{gate_id}/confirm-schedule-impact", response_model=ApprovalGateActionOut)
def confirm_gate_schedule(gate_id: int, preview_token: str, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    return _handle(lambda: confirm_approval_schedule(db, gate_id, preview_token, get_current_user(token, db)))


@router.get("/approval-gates/pending-forecast", response_model=list[PendingApprovalSegmentOut])
def pending_approval_forecast(token: str = Depends(auth_token), db: Session = Depends(get_db)):
    """各仪器上"等签批通过才会排入"的工时，按工作日历铺成时间段。"""
    get_current_user(token, db)
    return pending_approval_segments(db)


def _handle(callback):
    try:
        return callback()
    except ApprovalGateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ApprovalGatePermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ApprovalGateInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

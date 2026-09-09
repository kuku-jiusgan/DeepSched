from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import User
from app.api.users import require_authenticated_user
from app.schemas.schemas import (
    ProjectPlanApplyRequest,
    ProjectPlanApplyResponse,
    ProjectPlanInsertConfirmRequest,
)
from app.services.project_plan_apply_service import (
    ProjectPlanInvalidError,
    ProjectPlanNotFoundError,
    apply_project_plan,
    confirm_project_plan_insert,
)
from app.services.access_control_service import (
    AccessDeniedError,
    AccessResourceNotFoundError,
    require_project_editor,
)
from app.services.schedule_conflict_service import ScheduleConflictError
from app.services.schedule_run_lock_service import ScheduleBusyError
from app.services.schedule_request_service import enqueue_schedule_request
from app.services.schedule_request_service import get_schedule_request, cancel_schedule_request, request_message
from app.schemas.schedule_request_schemas import ScheduleRequestOut
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


@router.post("/apply-project-plan", response_model=ProjectPlanApplyResponse)
def apply_saved_project_plan(
    data: ProjectPlanApplyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    _ensure_project_editor(db, data.project_id, user)
    try:
        return apply_project_plan(db, data.project_id)
    except ProjectPlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProjectPlanInvalidError as exc:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "message": str(exc),
                "schedule_failure": exc.schedule_failure,
            },
        )
    except ScheduleConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"排程失败：{exc}")
    except ScheduleBusyError as exc:
        db.rollback()
        request = enqueue_schedule_request(db, data.project_id, user.id)
        return ProjectPlanApplyResponse(
            status="queued", project_id=data.project_id, request_id=request.id,
            message=request_message(request),
        )
    except Exception:
        db.rollback()
        logger.exception("项目计划排程失败 project_id=%s", data.project_id)
        raise HTTPException(status_code=500, detail="项目排程失败，请查看服务器日志获取具体原因")


@router.post("/apply-project-plan/confirm-insert", response_model=ProjectPlanApplyResponse)
def confirm_saved_project_plan_insert(
    data: ProjectPlanInsertConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    _ensure_project_editor(db, data.project_id, user)
    try:
        return confirm_project_plan_insert(db, data)
    except ProjectPlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProjectPlanInvalidError as exc:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "message": str(exc),
                "schedule_failure": exc.schedule_failure,
            },
        )
    except ScheduleConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"排程失败：{exc}")
    except ScheduleBusyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("项目计划确认插单失败 project_id=%s", data.project_id)
        raise HTTPException(status_code=500, detail="项目排程失败，请查看服务器日志获取具体原因")


@router.get("/schedule-requests/{request_id}", response_model=ScheduleRequestOut)
def get_schedule_request_status(request_id: str, db: Session = Depends(get_db), user: User = Depends(require_authenticated_user)):
    request = get_schedule_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="排程请求不存在")
    if request.requested_by not in {None, user.id} and user.role != "系统管理员":
        raise HTTPException(status_code=403, detail="无权查看该排程请求")
    return ScheduleRequestOut(
        id=request.id, project_id=request.project_id, request_type=request.request_type,
        priority=request.priority, status=request.status, message=request_message(request),
        result=request.result, error_message=request.error_message,
        created_at=request.created_at, started_at=request.started_at,
        finished_at=request.finished_at,
    )


@router.post("/schedule-requests/{request_id}/cancel", response_model=ScheduleRequestOut)
def cancel_schedule_request_status(request_id: str, db: Session = Depends(get_db), user: User = Depends(require_authenticated_user)):
    request = get_schedule_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="排程请求不存在")
    if request.requested_by not in {None, user.id} and user.role != "系统管理员":
        raise HTTPException(status_code=403, detail="无权取消该排程请求")
    request = cancel_schedule_request(db, request_id)
    return ScheduleRequestOut(
        id=request.id, project_id=request.project_id, request_type=request.request_type,
        priority=request.priority, status=request.status, message=request_message(request),
        result=request.result, error_message=request.error_message,
        created_at=request.created_at, started_at=request.started_at,
        finished_at=request.finished_at,
    )


def _ensure_project_editor(db: Session, project_id: int, user: User) -> None:
    try:
        require_project_editor(db, project_id, user)
    except AccessResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

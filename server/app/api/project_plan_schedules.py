from fastapi import APIRouter, Depends, HTTPException
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
        raise HTTPException(status_code=409, detail=str(exc))
    except ScheduleConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"排程失败：{exc}")
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
        raise HTTPException(status_code=409, detail=str(exc))
    except ScheduleConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"排程失败：{exc}")
    except Exception:
        db.rollback()
        logger.exception("项目计划确认插单失败 project_id=%s", data.project_id)
        raise HTTPException(status_code=500, detail="项目排程失败，请查看服务器日志获取具体原因")


def _ensure_project_editor(db: Session, project_id: int, user: User) -> None:
    try:
        require_project_editor(db, project_id, user)
    except AccessResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

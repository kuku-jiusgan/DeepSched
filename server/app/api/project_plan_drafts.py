import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.users import auth_token, get_current_user
from app.core.database import get_db
from app.schemas.project_plan_draft_schemas import ProjectPlanDraftCommitIn, ProjectPlanDraftCommitOut, ProjectPlanSaveAndScheduleRequest
from app.schemas.schemas import (
    ProjectPlanApplyResponse,
    ScheduleDeadlineRecommendationJobResponse,
)
from app.services.project_plan_draft_service import (
    ProjectPlanDraftInvalidError,
    ProjectPlanDraftNotFoundError,
    ProjectPlanDraftPermissionError,
    commit_project_plan_drafts,
    save_and_schedule_project_plan,
)
from app.services.schedule_deadline_recommendation_job_service import (
    get_deadline_recommendation_job,
)
from app.services.schedule_run_lock_service import ScheduleBusyError


router = APIRouter(prefix="/api/v1/projects", tags=["project-plan-drafts"])
logger = logging.getLogger(__name__)


@router.post("/{project_id}/plan-drafts/save-and-schedule", response_model=ProjectPlanApplyResponse)
def save_and_schedule(project_id: int, data: ProjectPlanSaveAndScheduleRequest, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    try:
        return save_and_schedule_project_plan(db, project_id, data, get_current_user(token, db))
    except ProjectPlanDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProjectPlanDraftPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ProjectPlanDraftInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ScheduleBusyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("项目计划保存并排程失败 project_id=%s", project_id)
        raise HTTPException(status_code=500, detail="项目排程失败，请查看服务器日志获取具体原因")


@router.get(
    "/{project_id}/plan-drafts/deadline-recommendations/{job_id}",
    response_model=ScheduleDeadlineRecommendationJobResponse,
)
def get_deadline_recommendation(project_id: int, job_id: str, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    get_current_user(token, db)
    result = get_deadline_recommendation_job(db, project_id, job_id)
    if not result:
        raise HTTPException(status_code=404, detail="延期建议任务不存在")
    return result


@router.post("/{project_id}/plan-drafts/commit", response_model=ProjectPlanDraftCommitOut)
def commit_drafts(project_id: int, data: ProjectPlanDraftCommitIn, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    try:
        return commit_project_plan_drafts(db, project_id, data, get_current_user(token, db))
    except ProjectPlanDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProjectPlanDraftPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ProjectPlanDraftInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

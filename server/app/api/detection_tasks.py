from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.access import require_management_user
from app.api.projects import _task_to_out
from app.api.users import require_authenticated_user
from app.core.database import get_db
from app.schemas.schemas import DetectionTaskCreate, DetectionTaskOut
from pydantic import BaseModel, Field
from app.services.detection_task_service import (
    DetectionTaskInvalidError,
    DetectionTaskNotFoundError,
    create_detection_task,
    confirm_detection_task_insert,
    delete_detection_task,
    list_detection_tasks,
    update_detection_task,
)
from app.services.project_actual_hours_service import project_actual_hours_map

router = APIRouter(prefix="/api/v1/detection-tasks", tags=["detection-tasks"])


def _response(project, db, schedule=None, actual_hours: float | None = None) -> dict:
    return DetectionTaskOut(
        id=project.id, project_id=project.id, code=project.code, name=project.name,
        client_name=project.client_name, priority=project.priority,
        manager_id=project.manager_id, manager_name=project.manager_name,
        start_date=project.start_date, end_date=project.end_date,
        task=_task_to_out(project.tasks[0], db),
        actual_hours=actual_hours if actual_hours is not None else project_actual_hours_map(db, [project]).get(project.id, 0),
        schedule_status=(schedule or {}).get("status"),
        schedule_message=(schedule or {}).get("message"),
        preview_token=(schedule or {}).get("preview_token"),
        project_impacts=(schedule or {}).get("project_impacts") or [],
        schedule_failure=(schedule or {}).get("schedule_failure"),
    ).model_dump()


@router.get("", response_model=list[DetectionTaskOut])
def get_detection_tasks(db: Session = Depends(get_db), user=Depends(require_authenticated_user)):
    projects = list_detection_tasks(db, user)
    actual_hours = project_actual_hours_map(db, projects)
    return [_response(item, db, actual_hours=actual_hours.get(item.id, 0)) for item in projects]


@router.post("", response_model=DetectionTaskOut)
def add_detection_task(data: DetectionTaskCreate, db: Session = Depends(get_db), user=Depends(require_management_user)):
    try:
        project, schedule = create_detection_task(db, data, user)
        return _response(project, db, schedule)
    except DetectionTaskInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/{detection_id}", response_model=DetectionTaskOut)
def edit_detection_task(detection_id: int, data: DetectionTaskCreate, db: Session = Depends(get_db), user=Depends(require_management_user)):
    try:
        project, schedule = update_detection_task(db, detection_id, data, user)
        return _response(project, db, schedule)
    except DetectionTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DetectionTaskInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


class DetectionTaskInsertConfirmRequest(BaseModel):
    preview_token: str = Field(min_length=1)


@router.post("/{detection_id}/confirm-insert", response_model=DetectionTaskOut)
def confirm_detection_insert(
    detection_id: int,
    data: DetectionTaskInsertConfirmRequest,
    db: Session = Depends(get_db),
    user=Depends(require_management_user),
):
    try:
        project, schedule = confirm_detection_task_insert(
            db, detection_id, data.preview_token, user,
        )
        return _response(project, db, schedule)
    except DetectionTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DetectionTaskInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{detection_id}")
def remove_detection_task(detection_id: int, db: Session = Depends(get_db), user=Depends(require_management_user)):
    try:
        delete_detection_task(db, detection_id, user)
        return {"detail": "已删除"}
    except DetectionTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DetectionTaskInvalidError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

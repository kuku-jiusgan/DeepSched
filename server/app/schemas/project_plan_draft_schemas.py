from pydantic import BaseModel, Field
from app.schemas.schemas import ProjectPlanApplyResponse


class ProjectPlanDraftTaskIn(BaseModel):
    client_id: int = Field(lt=0)
    name: str
    task_type: str
    requires_instrument: bool = False
    requires_human: bool = True
    estimated_hours: float | None = None
    switchover_hours: float = 0
    assignee_id: int | None = None
    parent_id: int | None = None
    predecessor_ids: list[int] = []
    instrument_ids: list[int] = []
    is_external_gate: bool = False
    plan_order: int = 0


class ProjectPlanDraftCommitIn(BaseModel):
    tasks: list[ProjectPlanDraftTaskIn] = Field(min_length=1)


class ProjectPlanDraftIdMap(BaseModel):
    client_id: int
    task_id: int


class ProjectPlanDraftCommitOut(BaseModel):
    status: str
    message: str
    created: int
    id_map: list[ProjectPlanDraftIdMap]

class ProjectPlanSaveAndScheduleRequest(BaseModel):
    tasks: list[ProjectPlanDraftTaskIn] = Field(default_factory=list)

class ProjectPlanSaveAndScheduleResponse(ProjectPlanApplyResponse):
    created: int = 0
    id_map: list[ProjectPlanDraftIdMap] = []

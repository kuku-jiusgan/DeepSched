from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.models import AuditLog, Project, Task, TaskDependency, TaskTypeConfig, User
from app.schemas.project_plan_template_schemas import StandardPlanImportOut, StandardPlanTaskOut
from app.services.project_access_service import FULL_PROJECT_ACCESS_ROLES
from app.services.project_hours_validation_service import validate_project_estimated_hours
from app.services.user_role_service import has_any_role
from app.services.task_dependency_service import create_continuous_successor


TEMPLATE_STEPS = [
    ("方法开发", "FFKF_001", Decimal("0.70"), True),
    ("方案撰写", "QCFA_001", Decimal("0.05"), False),
    ("方法验证", "FFYZ_001", Decimal("0.20"), True),
    ("报告撰写", "ZXBG_001", Decimal("0.05"), False),
]


class ProjectPlanTemplateNotFoundError(Exception):
    pass


class ProjectPlanTemplatePermissionError(Exception):
    pass


class ProjectPlanTemplateInvalidError(Exception):
    pass


def import_standard_plan(db, project_id: int, user: User) -> StandardPlanImportOut:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ProjectPlanTemplateNotFoundError("项目不存在")
    if not has_any_role(user, FULL_PROJECT_ACCESS_ROLES) and project.manager_id != user.id:
        raise ProjectPlanTemplatePermissionError("无权导入该项目的计划模板")
    if not project.estimated_hours or project.estimated_hours <= 0:
        raise ProjectPlanTemplateInvalidError("请先填写项目预计工时")
    if not project.manager_id:
        raise ProjectPlanTemplateInvalidError("请先设置项目负责人")

    required_codes = {step[1] for step in TEMPLATE_STEPS}
    active_codes = {
        item.code for item in db.query(TaskTypeConfig).filter(
            TaskTypeConfig.code.in_(required_codes),
            TaskTypeConfig.is_active.is_(True),
        ).all()
    }
    missing_codes = required_codes - active_codes
    if missing_codes:
        raise ProjectPlanTemplateInvalidError(
            f"标准任务类型未启用：{', '.join(sorted(missing_codes))}"
        )

    group_count = db.query(Task.id).filter(
        Task.project_id == project_id,
        Task.parent_id.is_(None),
        Task.task_type == "group",
    ).count()
    group = Task(
        project_id=project.id,
        name=f"标准计划{group_count + 1}",
        task_type="group",
        requires_instrument=False,
        requires_human=False,
        est_duration_hours=None,
        switchover_hours=0,
        status="pending",
        schedule_dirty=False,
    )
    db.add(group)
    db.flush()

    allocations = _allocate_hours(float(project.estimated_hours))
    created_tasks: list[Task] = []
    for index, ((name, task_type, _, requires_instrument), hours) in enumerate(zip(TEMPLATE_STEPS, allocations)):
        task = Task(
            project_id=project.id,
            name=name,
            task_type=task_type,
            requires_instrument=requires_instrument,
            requires_human=True,
            est_duration_hours=hours,
            switchover_hours=0,
            assignee_id=project.manager_id,
            status="pending",
            schedule_dirty=True,
            parent_id=group.id,
            instrument_ids=[],
            plan_order=index if index < 2 else index + 1,
        )
        db.add(task)
        db.flush()
        created_tasks.append(task)

    method_task, scheme_task, validation_task, report_task = created_tasks
    restriction = Task(
        project_id=project.id,
        name="方案签批",
        task_type="approval_gate",
        requires_instrument=False,
        requires_human=False,
        est_duration_hours=None,
        switchover_hours=0,
        assignee_id=project.manager_id,
        status="waiting_external",
        is_external_gate=True,
        gate_status="not_submitted",
        schedule_dirty=False,
        parent_id=group.id,
        plan_order=2,
    )
    db.add(restriction)
    db.flush()
    validation_task.status = "waiting_external"
    validation_task.schedule_dirty = False
    report_task.status = "waiting_external"
    report_task.schedule_dirty = False

    db.add_all([
        create_continuous_successor(method_task, scheme_task),
        TaskDependency(task_id=restriction.id, predecessor_id=scheme_task.id),
        TaskDependency(task_id=validation_task.id, predecessor_id=restriction.id),
        create_continuous_successor(validation_task, report_task),
    ])
    validate_project_estimated_hours(db, project.id)
    db.add(AuditLog(
        user_name=user.display_name or user.username,
        action="standard_project_plan_imported",
        target_type="project",
        target_id=project.id,
        detail={
            "estimated_hours": float(project.estimated_hours),
            "group_task_id": group.id,
            "task_ids": [task.id for task in created_tasks],
            "approval_restriction_id": restriction.id,
        },
    ))
    db.commit()
    return StandardPlanImportOut(
        status="ok",
        message=f"已生成“{group.name}”及其 5 个子任务",
        project_id=project.id,
        estimated_hours=float(project.estimated_hours),
        tasks=[
            StandardPlanTaskOut(
                id=task.id,
                name=task.name,
                task_type=task.task_type,
                percentage=float(TEMPLATE_STEPS[index][2] * 100),
                estimated_hours=float(task.est_duration_hours or 0),
            )
            for index, task in enumerate(created_tasks[:2])
        ] + [
            StandardPlanTaskOut(
                id=restriction.id,
                name=restriction.name,
                task_type=restriction.task_type,
                is_approval_restriction=True,
            ),
            *[
                StandardPlanTaskOut(
                    id=task.id,
                    name=task.name,
                    task_type=task.task_type,
                    percentage=float(TEMPLATE_STEPS[index][2] * 100),
                    estimated_hours=float(task.est_duration_hours or 0),
                )
                for index, task in enumerate(created_tasks[2:], start=2)
            ],
        ],
    )


def _allocate_hours(total_hours: float) -> list[float]:
    total = Decimal(str(total_hours))
    allocations = [
        (total * percentage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for _, _, percentage, _ in TEMPLATE_STEPS[:-1]
    ]
    allocations.append(total - sum(allocations, Decimal("0")))
    return [float(max(Decimal("0"), value)) for value in allocations]

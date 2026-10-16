from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    Instrument, Project, Task, TaskDependency, TaskExecutionSegment, TimeSlot, User,
)
from app.services import scheduler
from app.services.task_pause_horizon_service import solver_horizon_end
from app.services.task_pause_service import pause_and_switch_task
from app.services.task_pause_switch_context_service import build_pause_switch_context
from app.services.task_pause_switch_diagnosis_service import diagnose_pause_switch_failure


@pytest.fixture
def switch_plan():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    tomorrow = (datetime.now() + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0,
    )
    now = tomorrow + timedelta(days=(7 - tomorrow.weekday()) % 7)
    branch_end = now + timedelta(days=16)
    deadline = (now + timedelta(days=30)).replace(hour=23, minute=59, second=59)
    operator = User(id=1, username="source", display_name="源负责人", role="技术员")
    db.add_all([
        operator, User(id=2, username="target", display_name="接替负责人", role="技术员"),
        Instrument(id=1, code="LCMS", name="液质"),
        Instrument(id=2, code="GCMS", name="气质",
                   effective_work_start="09:00", effective_work_end="17:00"),
        Project(id=1, code="SOURCE", name="检测项目", end_date=deadline),
        Project(id=2, code="TARGET", name="多分支项目", end_date=deadline),
    ])
    db.flush()
    source = Task(
        id=1, project_id=1, name="检测", task_type="manual", status="running",
        est_duration_hours=8, requires_instrument=True, requires_human=True,
        instrument_ids=[1], assignee_id=1,
    )
    target = Task(
        id=2, project_id=2, name="方法开发", task_type="manual", status="scheduled",
        est_duration_hours=4, requires_instrument=True, requires_human=True,
        instrument_ids=[1], assignee_id=2,
    )
    drafting = Task(
        id=3, project_id=2, name="另一分支方案撰写", task_type="manual",
        status="scheduled", est_duration_hours=1, requires_human=True, assignee_id=2,
    )
    gate = Task(
        id=4, project_id=2, name="另一分支方案签批", task_type="approval_gate",
        status="waiting_external", is_external_gate=True, gate_status="not_submitted",
        requires_human=False,
    )
    validation = Task(
        id=5, project_id=2, name="另一分支方法验证", task_type="manual",
        status="waiting_external", est_duration_hours=16, requires_instrument=True,
        requires_human=True, instrument_ids=[2], assignee_id=2,
    )
    report = Task(
        id=6, project_id=2, name="另一分支报告撰写", task_type="manual",
        status="waiting_external", est_duration_hours=2, requires_human=True,
        assignee_id=2,
    )
    db.add_all([source, target, drafting, gate, validation, report])
    db.flush()
    source_slot = TimeSlot(
        id=1, task_id=1, instrument_id=1, status="running", tier="confirmed",
        plan_start=now - timedelta(hours=1), plan_end=now + timedelta(hours=7),
        actual_start=now - timedelta(hours=1),
    )
    target_slot = TimeSlot(
        id=2, task_id=2, instrument_id=1, status="scheduled", tier="confirmed",
        plan_start=now + timedelta(hours=7), plan_end=now + timedelta(hours=11),
    )
    db.add_all([
        source_slot, target_slot,
        TimeSlot(id=3, task_id=3, status="scheduled", tier="confirmed",
                 plan_start=branch_end - timedelta(hours=1), plan_end=branch_end),
        TaskDependency(task_id=4, predecessor_id=3),
        TaskDependency(task_id=5, predecessor_id=4),
        TaskDependency(task_id=6, predecessor_id=5),
        TaskExecutionSegment(task_id=1, slot_id=1, instrument_id=1, operator_id=1,
                             started_at=now - timedelta(hours=1)),
    ])
    db.commit()
    yield db, now, branch_end, operator, source_slot, target_slot
    db.close()
    engine.dispose()


def _switch(plan):
    db, now, _, operator, source_slot, target_slot = plan
    with patch("app.services.task_pause_service.datetime") as clock:
        clock.now.return_value = now
        return pause_and_switch_task(db, source_slot.id, "切换", operator, target_slot.id)


@pytest.mark.parametrize("has_deadline", [True, False])
def test_switch_covers_late_branch_without_changing_deadlines(switch_plan, has_deadline):
    db, _, branch_end, _, source_slot, target_slot = switch_plan
    if not has_deadline:
        for project in db.query(Project).all():
            project.end_date = None
        db.commit()
    deadlines = {project.id: project.end_date for project in db.query(Project).all()}
    fixed_slot = db.get(TimeSlot, 3)
    fixed_window = (fixed_slot.plan_start, fixed_slot.plan_end)

    with patch(
        "app.services.scheduler.build_task_variables", wraps=scheduler.build_task_variables,
    ) as solver_input:
        result = _switch(switch_plan)

    assert result["status"] == "ok"
    assert {task.id for task in solver_input.call_args.kwargs["tasks"]} == {1, 2, 5, 6}
    assert source_slot.task.status == "paused"
    assert target_slot.task.status == "running"
    source_slots = db.query(TimeSlot).filter(
        TimeSlot.task_id == source_slot.task_id, TimeSlot.lifecycle_status == "active",
        TimeSlot.actual_start.is_(None),
    ).all()
    assert source_slots
    assert sum((slot.plan_end - slot.plan_start).total_seconds() / 60
               for slot in source_slots) == 7 * 60
    assert {project.id: project.end_date for project in db.query(Project).all()} == deadlines
    assert (fixed_slot.plan_start, fixed_slot.plan_end) == fixed_window
    assert fixed_slot.plan_end == branch_end
    assert db.query(TimeSlot).filter(TimeSlot.task_id.in_([4, 5, 6])).count() == 0
    assert db.get(Task, 5).status == "waiting_external"
    assert db.get(Task, 6).status == "waiting_external"


def test_horizon_covers_work_after_late_approval_on_instrument_calendar(switch_plan):
    db, now, branch_end, _, source_slot, target_slot = switch_plan
    for project in db.query(Project).all():
        project.end_date = None
    # 周五签批，16 小时验证必须跨过周末，使用气质的 09:00–17:00 时段。
    approval_at = branch_end + timedelta(days=2, hours=7)
    db.get(Task, 4).expected_approval_at = approval_at
    db.flush()
    context = build_pause_switch_context(db, source_slot, target_slot, now)

    horizon_end = solver_horizon_end(db, context)

    assert approval_at.weekday() == 4
    assert horizon_end >= approval_at + timedelta(days=4)
    result = _switch(switch_plan)
    assert result["status"] == "ok"


def test_late_branch_deadline_overrun_is_not_a_horizon_conflict(switch_plan):
    db, now, branch_end, _, source_slot, target_slot = switch_plan
    db.get(Project, 1).end_date = now + timedelta(days=2)
    db.get(Project, 2).end_date = branch_end - timedelta(days=1)
    db.commit()
    deadlines = {project.id: project.end_date for project in db.query(Project).all()}

    failure = diagnose_pause_switch_failure(db, source_slot, target_slot, now)

    assert failure["pause_switch_failure"]["kind"] == "project_deadline_overrun"
    overruns = failure["pause_switch_failure"]["overruns"]
    assert [row["project_id"] for row in overruns] == [2]
    assert overruns[0]["blocking_task_name"] == "另一分支报告撰写"
    assert {project.id: project.end_date for project in db.query(Project).all()} == deadlines
    assert source_slot.task.status == "running"
    assert target_slot.task.status == "scheduled"


def test_switch_preserves_seconds_at_a_half_hour_boundary(switch_plan):
    db, now, branch_end, operator, source_slot, target_slot = switch_plan
    switch_time = now + timedelta(seconds=27)

    result = _switch((db, switch_time, branch_end, operator, source_slot, target_slot))
    db.flush()

    assert result["status"] == "ok"
    assert source_slot.actual_end == switch_time
    running = db.query(TimeSlot).filter(
        TimeSlot.task_id == target_slot.task_id, TimeSlot.lifecycle_status == "active",
        TimeSlot.status == "running",
    ).one()
    assert running.actual_start == switch_time
    assert running.plan_start == now + timedelta(minutes=30)

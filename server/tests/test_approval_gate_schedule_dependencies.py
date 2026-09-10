import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot, User
from app.services.approval_gate_schedule_dependencies import (
    build_approval_insert_dependencies,
)
from app.services.approval_gate_schedule_context import ApprovalScheduleContext
from app.services.project_plan_apply_service import _execute_replan
from app.services.project_plan_errors import ProjectPlanInvalidError
from app.services.task_dependency_service import create_continuous_successor


class ApprovalGateScheduleDependenciesTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(id=1, username="analyst", display_name="分析员", role="分析员")
        self.instrument = Instrument(id=1, code="ICPMS-01", name="ICP-MS")
        self.approved_project = Project(id=1, code="XM2026220", name="已签批项目")
        self.existing_project = Project(id=2, code="XM2026224", name="原排程项目")
        self.db.add_all([
            self.user,
            self.instrument,
            self.approved_project,
            self.existing_project,
        ])
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def _running_chain(self):
        start = (datetime.now() + timedelta(days=1)).replace(
            hour=8, minute=30, second=0, microsecond=0,
        )
        start += timedelta(days=(7 - start.weekday()) % 7)
        group = Task(project=self.existing_project, name="标准计划", task_type="group")
        development = Task(
            project=self.existing_project, parent=group, name="方法开发",
            task_type="FFKF_001", status="running", requires_instrument=True,
            requires_human=True, instrument_ids=[1], assignee=self.user,
            est_duration_hours=4,
        )
        writing = Task(
            project=self.existing_project, parent=group, name="方案撰写",
            task_type="QCFA_001", status="scheduled", requires_instrument=False,
            requires_human=True, assignee=self.user, est_duration_hours=2,
        )
        inserted = Task(
            project=self.approved_project, name="方法验证", task_type="FFYZ_001",
            status="pending", requires_instrument=True, instrument_ids=[1],
            requires_human=True, assignee=self.user, est_duration_hours=4,
        )
        self.db.add_all([group, development, writing, inserted])
        self.db.flush()
        dependency = create_continuous_successor(development, writing)
        self.db.add_all([
            dependency,
            TimeSlot(task=development, instrument_id=1, plan_start=start,
                     plan_end=start + timedelta(hours=4), actual_start=start,
                     status="running", tier="confirmed"),
            TimeSlot(task=writing, plan_start=start + timedelta(hours=4),
                     plan_end=start + timedelta(hours=6), status="scheduled",
                     tier="confirmed"),
        ])
        self.db.commit()
        return start, development, writing, inserted, dependency

    def test_running_method_finishes_writing_before_approved_work_in_real_replan(self):
        start, development, writing, inserted, _ = self._running_chain()
        context = ApprovalScheduleContext(
            gate_id=100, downstream_task_ids={inserted.id},
            branch_task_ids={inserted.id}, anchor_at=start + timedelta(hours=4),
        )
        with patch("app.services.planning_problem.time_horizon", return_value=(
            start, start + timedelta(days=7), 7 * 48,
        )):
            result = _execute_replan(
                self.db, self.approved_project, [inserted], [writing],
                commit=False, approval_context=context,
            )

        self.assertEqual("applied", result.status, result.message)
        writing_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == writing.id, TimeSlot.lifecycle_status == "active",
        ).all()
        inserted_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == inserted.id, TimeSlot.lifecycle_status == "active",
        ).all()
        self.assertEqual(start + timedelta(hours=4), min(s.plan_start for s in writing_slots))
        self.assertLessEqual(max(s.plan_end for s in writing_slots),
                             min(s.plan_start for s in inserted_slots))
        self.assertEqual("running", development.status)

    def test_ordinary_predecessor_does_not_reserve_continuous_queue_position(self):
        _, _, writing, inserted, dependency = self._running_chain()
        dependency.dependency_type = "predecessor"
        self.db.flush()
        self.assertEqual([], build_approval_insert_dependencies(self.db, [inserted], [writing]))

    def test_invalid_continuous_group_fails_explicitly(self):
        _, _, writing, inserted, _ = self._running_chain()
        writing.parent_id = None
        self.db.flush()
        with self.assertRaisesRegex(ProjectPlanInvalidError, "连续后续关系"):
            build_approval_insert_dependencies(self.db, [inserted], [writing])

    def test_shared_assignee_is_protected_even_on_different_instruments(self):
        _, _, writing, inserted, _ = self._running_chain()
        inserted.instrument_ids = [2]
        self.assertEqual([(inserted.id, writing.id)],
                         build_approval_insert_dependencies(self.db, [inserted], [writing]))

    def test_approval_insert_stays_before_existing_method_scheme_pair(self):
        group = Task(
            project=self.existing_project,
            name="标准计划",
            task_type="group",
            requires_human=False,
        )
        inserted = Task(
            project=self.approved_project,
            name="方法验证",
            task_type="FFYZ_001",
            requires_instrument=True,
            requires_human=True,
            assignee=self.user,
            instrument_ids=[self.instrument.id],
        )
        development = Task(
            project=self.existing_project,
            parent=group,
            name="方法开发",
            task_type="FFKF_001",
            requires_instrument=True,
            requires_human=True,
            assignee=self.user,
            instrument_ids=[self.instrument.id],
        )
        writing = Task(
            project=self.existing_project,
            parent=group,
            name="方案撰写",
            task_type="QCFA_001",
            requires_human=True,
            assignee=self.user,
        )
        self.db.add_all([group, inserted, development, writing])
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=writing.id,
            predecessor_id=development.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()

        dependencies = build_approval_insert_dependencies(
            self.db,
            [inserted],
            [development, writing],
        )

        self.assertEqual([(development.id, inserted.id)], dependencies)

    def test_unrelated_resource_does_not_add_queue_dependency(self):
        group = Task(
            project=self.existing_project,
            name="标准计划",
            task_type="group",
            requires_human=False,
        )
        inserted = Task(
            project=self.approved_project,
            name="方法验证",
            task_type="FFYZ_001",
            requires_instrument=True,
            requires_human=True,
            assignee=self.user,
            instrument_ids=[2],
        )
        development = Task(
            project=self.existing_project,
            parent=group,
            name="方法开发",
            task_type="FFKF_001",
            requires_instrument=True,
            requires_human=False,
            instrument_ids=[self.instrument.id],
        )
        writing = Task(
            project=self.existing_project,
            parent=group,
            name="方案撰写",
            task_type="QCFA_001",
            requires_human=False,
        )
        self.db.add_all([group, inserted, development, writing])
        self.db.flush()
        self.db.add(TaskDependency(
            task_id=writing.id,
            predecessor_id=development.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()

        dependencies = build_approval_insert_dependencies(
            self.db,
            [inserted],
            [development, writing],
        )

        self.assertEqual([], dependencies)

    def test_manual_selected_task_stays_before_movable_instrument_task(self):
        selected = Task(
            project=self.approved_project,
            name="报告撰写",
            task_type="ZXBG_001",
            requires_human=True,
            assignee=self.user,
        )
        movable = Task(
            project=self.existing_project,
            name="方法开发",
            task_type="FFKF_001",
            requires_instrument=True,
            requires_human=True,
            assignee=self.user,
            instrument_ids=[self.instrument.id],
        )
        self.db.add_all([selected, movable])
        self.db.commit()

        self.assertEqual(
            [(movable.id, selected.id)],
            build_approval_insert_dependencies(self.db, [selected], [movable]),
        )


if __name__ == "__main__":
    unittest.main()

"""签批后工时按依赖和实际资源测算，不能把并行分支统一串行倒扣。"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, InstrumentBridgeReservation, Project, Task, TaskDependency, TimeSlot, User
from app.services.scheduler import SchedulerService


class ApprovalResourceCapacityTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.start = datetime(2026, 10, 12, 8, 30)
        self.project = Project(
            code="PARALLEL", name="并行签批分支", estimated_hours=24,
            start_date=self.start, end_date=self.start + timedelta(days=1, hours=8),
        )
        self.instruments = [
            Instrument(code=f"CAPACITY-{index}", name=f"仪器{index}")
            for index in range(2)
        ]
        self.people = [
            User(username=f"capacity-{index}", display_name=f"分析员{index}", role="技术员")
            for index in range(2)
        ]
        self.db.add_all([self.project, *self.instruments, *self.people])
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _branch(self, index, instrument, assignee, develop_hours=4, verify_hours=8):
        group = Task(
            project=self.project, name=f"分支{index}", task_type="group",
            requires_human=False, requires_instrument=False,
            est_duration_hours=develop_hours + verify_hours,
        )
        self.db.add(group)
        self.db.flush()
        develop = Task(
            project=self.project, parent_id=group.id, name=f"开发{index}",
            task_type="test", requires_instrument=True, requires_human=True,
            instrument_ids=[instrument.id], assignee_id=assignee.id,
            est_duration_hours=develop_hours, status="pending",
        )
        gate = Task(
            project=self.project, parent_id=group.id, name=f"签批{index}",
            task_type="approval_gate", is_external_gate=True,
            requires_human=False, requires_instrument=False,
            gate_status="not_submitted", status="waiting_external",
        )
        verify = Task(
            project=self.project, parent_id=group.id, name=f"验证{index}",
            task_type="test", requires_instrument=True, requires_human=True,
            instrument_ids=[instrument.id], assignee_id=assignee.id,
            est_duration_hours=verify_hours, status="waiting_external",
        )
        self.db.add_all([develop, gate, verify])
        self.db.flush()
        self.db.add_all([
            TaskDependency(task_id=gate.id, predecessor_id=develop.id),
            TaskDependency(task_id=verify.id, predecessor_id=gate.id),
        ])
        self.db.flush()
        return develop, gate, verify

    def _existing_occupancy(self, person):
        project = Project(code="EXISTING", name="已有人员占用")
        task = Task(
            project=project, name="运行中工作", task_type="test", status="running",
            requires_instrument=False, requires_human=True, assignee_id=person.id,
            est_duration_hours=4,
        )
        slot = TimeSlot(
            task=task, plan_start=self.start, plan_end=self.start + timedelta(hours=4),
            actual_start=self.start, tier="confirmed", status="running",
        )
        self.db.add_all([project, task, slot])
        self.db.flush()
        return slot

    def _generate(self, task_ids=None, **kwargs):
        with patch("app.services.planning_problem.time_horizon", return_value=(
            self.start, self.start + timedelta(days=14), 14 * 48,
        )):
            return SchedulerService(self.db).generate(
                project_ids=[self.project.id], task_ids=task_ids,
                current_project_id=self.project.id, commit=False,
                include_failure_diagnostics=False, solver_time_limit=5, **kwargs,
            )

    def test_parallel_branches_can_start_after_existing_person_releases(self):
        branches = [
            self._branch(index, self.instruments[index], self.people[index])
            for index in range(2)
        ]
        occupied = self._existing_occupancy(self.people[0])

        result = self._generate()

        self.assertEqual("ok", result["status"], result)
        slots = self.db.query(TimeSlot).filter(TimeSlot.task_id == branches[0][0].id).all()
        self.assertTrue(slots)
        self.assertGreaterEqual(min(slot.plan_start for slot in slots), occupied.plan_end)
        self.assertEqual("running", occupied.status)
        self.assertIsNone(occupied.actual_end)
        for _develop, gate, verify in branches:
            self.assertEqual("waiting_external", gate.status)
            self.assertEqual("waiting_external", verify.status)
            self.assertEqual(0, self.db.query(TimeSlot).filter(TimeSlot.task_id == verify.id).count())
        self.assertEqual(0, self.db.query(InstrumentBridgeReservation).count())

    def test_shared_instrument_prevents_false_parallel_capacity(self):
        for index in range(2):
            self._branch(index, self.instruments[0], self.people[index])

        result = self._generate()

        self.assertEqual("INFEASIBLE", result["solver_status"], result)
        self.assertEqual(0, self.db.query(TimeSlot).count())

    def test_shared_person_prevents_false_parallel_capacity(self):
        for index in range(2):
            self._branch(index, self.instruments[index], self.people[0])

        result = self._generate()

        self.assertEqual("INFEASIBLE", result["solver_status"], result)
        self.assertEqual(0, self.db.query(TimeSlot).count())

    def test_downstream_uses_instrument_calendar_instead_of_global_hours(self):
        self.project.end_date = self.start + timedelta(days=1, hours=11, minutes=30)
        self.project.estimated_hours = 6
        self.instruments[0].effective_work_end = "10:30"
        self._branch(0, self.instruments[0], self.people[0], develop_hours=2, verify_hours=4)

        result = self._generate()

        self.assertEqual("INFEASIBLE", result["solver_status"], result)
        self.assertEqual(0, self.db.query(TimeSlot).count())

    def test_task_selection_still_checks_other_branches_in_same_project(self):
        first = self._branch(0, self.instruments[0], self.people[0])
        self._branch(1, self.instruments[1], self.people[1])
        self.instruments[1].effective_work_end = "09:30"

        result = self._generate(task_ids=[first[0].id])

        self.assertEqual("INFEASIBLE", result["solver_status"], result)

    def test_immediate_approval_option_still_does_not_persist_unapproved_tasks(self):
        develop, _gate, verify = self._branch(0, self.instruments[0], self.people[0])

        result = self._generate(include_pending_approval_tasks=True)

        self.assertEqual("ok", result["status"], result)
        self.assertEqual("scheduled", develop.status)
        self.assertEqual("waiting_external", verify.status)
        self.assertEqual(0, self.db.query(TimeSlot).filter(TimeSlot.task_id == verify.id).count())

    def test_expected_approval_date_constrains_normal_schedule(self):
        _develop, gate, _verify = self._branch(0, self.instruments[0], self.people[0])
        gate.expected_approval_at = self.project.end_date

        result = self._generate()

        self.assertEqual("error", result["status"], result)
        self.assertEqual(0, self.db.query(TimeSlot).count())

    def test_approved_downstream_gets_slots_after_actual_approval(self):
        develop, gate, verify = self._branch(0, self.instruments[0], self.people[0])
        develop.status = "completed"
        gate.gate_status = "approved"
        gate.status = "completed"
        gate.approved_at = self.start + timedelta(hours=4)
        gate.expected_approval_at = self.project.end_date + timedelta(days=1)

        result = self._generate()

        self.assertEqual("ok", result["status"], result)
        self.assertEqual("scheduled", verify.status)
        slots = self.db.query(TimeSlot).filter(TimeSlot.task_id == verify.id).all()
        self.assertTrue(slots)
        self.assertGreaterEqual(min(slot.plan_start for slot in slots), gate.approved_at)

    def test_released_legacy_forecast_slot_is_superseded_without_replacement(self):
        develop, _gate, verify = self._branch(0, self.instruments[0], self.people[0])
        old_slot = TimeSlot(
            task=verify, instrument_id=self.instruments[0].id,
            plan_start=self.start, plan_end=self.start + timedelta(hours=8),
            tier="forecast", status="scheduled",
        )
        self.db.add(old_slot)
        self.db.flush()

        result = self._generate(
            released_slot_ids={old_slot.id}, replaceable_task_ids={develop.id, verify.id},
        )

        self.assertEqual("ok", result["status"], result)
        self.assertEqual("superseded", old_slot.lifecycle_status)
        self.assertEqual("waiting_external", verify.status)
        self.assertEqual(0, self.db.query(TimeSlot).filter(
            TimeSlot.task_id == verify.id, TimeSlot.lifecycle_status == "active",
        ).count())


if __name__ == "__main__":
    unittest.main()

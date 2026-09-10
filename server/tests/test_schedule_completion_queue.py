import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    Instrument,
    InstrumentFault,
    Project,
    ScheduleRule,
    Task,
    TaskDependency,
    TimeSlot,
    User,
)
from app.services.schedule_completion_service import (
    _forward_shift_instrument_queue,
)
from app.services.schedule_rule_service import sync_rules


class ScheduleCompletionQueueTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add_all([
            Instrument(id=1, code="BASE-I-001", name="基础测试仪器1"),
            Instrument(id=2, code="BASE-I-002", name="基础测试仪器2"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _add_projects(self, *project_ids: int) -> None:
        self.db.add_all([
            Project(
                id=project_id,
                code=f"TEST-P-{project_id:03d}",
                name=f"测试项目{project_id}",
                start_date=datetime(2026, 7, 1),
                end_date=datetime(2026, 8, 31),
            )
            for project_id in project_ids
        ])

    def _enable_maintenance_avoidance(self) -> None:
        sync_rules(self.db, commit=False)
        rule = self.db.query(ScheduleRule).filter(
            ScheduleRule.code == "maintenance_avoidance",
        ).one()
        rule.is_enabled = True
        self.db.flush()

    def test_non_instrument_forward_filters_by_assignee(self):
        current = Task(project_id=1, name="current", task_type="test", status="completed", assignee_id=7, requires_human=True)
        unrelated = Task(project_id=1, name="unrelated", task_type="test", status="scheduled", assignee_id=8, requires_human=True)
        following = Task(project_id=1, name="following", task_type="test", status="scheduled", assignee_id=7, requires_human=True)
        self.db.add_all([current, unrelated, following])
        self.db.flush()
        self.db.add_all([
            TimeSlot(task_id=unrelated.id, instrument_id=None, plan_start=datetime(2026, 7, 20, 8, 30), plan_end=datetime(2026, 7, 20, 10, 30), status="scheduled"),
            TimeSlot(task_id=following.id, instrument_id=None, plan_start=datetime(2026, 7, 20, 10, 30), plan_end=datetime(2026, 7, 20, 12, 30), status="scheduled"),
        ])
        self.db.commit()
        result = _forward_shift_instrument_queue(self.db, None, datetime(2026, 7, 20, 8, 30), 7)
        self.assertEqual(1, result["moved_tasks"])

    def test_non_instrument_completion_advances_other_project_instrument_task(self):
        self._add_projects(1, 2)
        bridge = Task(
            project_id=1, name="方案撰写", task_type="manual", status="scheduled",
            assignee_id=7, requires_human=True, requires_instrument=False,
        )
        successor = Task(
            project_id=2, name="方法开发", task_type="test", status="scheduled",
            assignee_id=7, requires_human=True, requires_instrument=True,
        )
        self.db.add_all([bridge, successor])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=bridge.id, instrument_id=None,
                plan_start=datetime(2026, 7, 20, 10, 30),
                plan_end=datetime(2026, 7, 20, 12, 30), status="scheduled",
            ),
            TimeSlot(
                task_id=successor.id, instrument_id=1,
                plan_start=datetime(2026, 7, 20, 15, 30),
                plan_end=datetime(2026, 7, 20, 17, 30), status="scheduled",
            ),
        ])
        self.db.commit()

        result = _forward_shift_instrument_queue(
            self.db, None, datetime(2026, 7, 20, 12, 30), 7, bridge.project_id,
        )

        moved_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == successor.id,
            TimeSlot.lifecycle_status == "active",
        ).one()
        self.assertEqual(1, result["moved_tasks"])
        self.assertLess(moved_slot.plan_start, datetime(2026, 7, 20, 15, 30))

    def test_forward_shift_includes_blocked_successor_with_scheduled_slot(self):
        current = Task(
            project_id=1, name="current", task_type="test", status="completed",
            assignee_id=7, requires_human=True,
        )
        successor = Task(
            project_id=2, name="successor", task_type="test", status="blocked",
            assignee_id=7, requires_human=True,
        )
        self.db.add_all([current, successor])
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=successor.id, instrument_id=None,
            plan_start=datetime(2026, 7, 20, 10, 30),
            plan_end=datetime(2026, 7, 20, 12, 30), status="scheduled",
        ))
        self.db.commit()

        result = _forward_shift_instrument_queue(
            self.db, None, datetime(2026, 7, 20, 8, 30), 7,
        )

        self.assertEqual(1, result["moved_tasks"])

    def test_completed_future_segments_do_not_block_forward_shift(self):
        completed = Task(project_id=1, name="done", task_type="test", status="done")
        next_task = Task(project_id=1, name="next", task_type="test", status="scheduled")
        self.db.add_all([completed, next_task])
        self.db.flush()
        self.db.add(TaskDependency(task_id=next_task.id, predecessor_id=completed.id))
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 20, 0),
                actual_start=datetime(2026, 7, 13, 8, 30),
                actual_end=datetime(2026, 7, 13, 12, 0),
                status="completed",
            ),
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 14, 8, 30),
                plan_end=datetime(2026, 7, 14, 20, 0),
                status="completed",
            ),
            TimeSlot(
                task_id=next_task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 15, 8, 30),
                plan_end=datetime(2026, 7, 15, 10, 30),
                status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        moved_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == next_task.id,
            TimeSlot.status == "scheduled",
            TimeSlot.lifecycle_status == "active",
        ).one()
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(datetime(2026, 7, 13, 12, 0), moved_slot.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 14, 0), moved_slot.plan_end)

    def test_forward_shift_compacts_instrument_queue_across_projects(self):
        first = Task(project_id=2, name="project-b", task_type="test", status="scheduled")
        second = Task(project_id=3, name="project-c", task_type="test", status="scheduled")
        self.db.add_all([first, second])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=first.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 15, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=second.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 17, 0),
                plan_end=datetime(2026, 7, 13, 19, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        slots = self.db.query(TimeSlot).filter(
            TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.plan_start).all()
        self.assertEqual(2, result["moved_tasks"])
        self.assertEqual([first.id, second.id], [slot.task_id for slot in slots])
        self.assertEqual(datetime(2026, 7, 13, 12, 0), slots[0].plan_start)
        self.assertEqual(datetime(2026, 7, 13, 14, 30), slots[1].plan_start)

    def test_forward_shift_moves_frozen_but_ignores_manual_and_running_tasks(self):
        manual = Task(project_id=1, name="manual", task_type="test", status="scheduled")
        frozen = Task(project_id=2, name="frozen", task_type="test", status="scheduled")
        partly_running = Task(
            project_id=3, name="partly-running", task_type="test", status="scheduled",
        )
        self.db.add_all([manual, frozen, partly_running])
        self.db.flush()
        original_start = datetime(2026, 7, 13, 16, 0)
        self.db.add_all([
            TimeSlot(
                task_id=manual.id, instrument_id=None,
                plan_start=datetime(2026, 7, 13, 14, 0),
                plan_end=datetime(2026, 7, 13, 15, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=frozen.id, instrument_id=1,
                plan_start=original_start,
                plan_end=datetime(2026, 7, 13, 18, 0),
                tier="frozen", status="scheduled",
            ),
            TimeSlot(
                task_id=partly_running.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 10, 0),
                plan_end=datetime(2026, 7, 13, 12, 0), status="running",
            ),
            TimeSlot(
                task_id=partly_running.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 18, 0),
                plan_end=datetime(2026, 7, 13, 19, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        frozen_slot = self.db.query(TimeSlot).filter(TimeSlot.task_id == frozen.id).one()
        self.assertEqual(0, result["moved_tasks"])
        self.assertEqual(original_start, frozen_slot.plan_start)
        self.assertEqual(1, self.db.query(TimeSlot).filter(TimeSlot.task_id == manual.id).count())
        self.assertEqual(
            2,
            self.db.query(TimeSlot).filter(TimeSlot.task_id == partly_running.id).count(),
        )

    def test_forward_shift_respects_dependency_and_human_availability(self):
        self._add_projects(1, 2, 3)
        self.db.add(User(
            id=7, username="assignee-7", display_name="负责人",
            role="分析员", is_active=True,
        ))
        predecessor = Task(project_id=1, name="predecessor", task_type="test", status="scheduled")
        candidate = Task(
            project_id=2, name="candidate", task_type="test", status="scheduled",
            requires_instrument=True, requires_human=True, assignee_id=7,
        )
        other_work = Task(
            project_id=3, name="other-work", task_type="test", status="scheduled",
            requires_instrument=True, requires_human=True, assignee_id=7,
        )
        self.db.add_all([predecessor, candidate, other_work])
        self.db.flush()
        self.db.add(TaskDependency(task_id=candidate.id, predecessor_id=predecessor.id))
        self.db.add_all([
            TimeSlot(
                task_id=predecessor.id, instrument_id=None,
                plan_start=datetime(2026, 7, 13, 11, 0),
                plan_end=datetime(2026, 7, 13, 14, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=other_work.id, instrument_id=2,
                plan_start=datetime(2026, 7, 13, 14, 0),
                plan_end=datetime(2026, 7, 13, 15, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=candidate.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 17, 0),
                plan_end=datetime(2026, 7, 13, 19, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(1, datetime(2026, 7, 13, 12, 0))

        moved_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == candidate.id,
            TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.id.desc()).first()
        self.assertIsNotNone(moved_slot)
        self.assertEqual(0, result["moved_tasks"])
        self.assertEqual(datetime(2026, 7, 13, 17, 0), moved_slot.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 19, 0), moved_slot.plan_end)

    def test_forward_shift_respects_open_instrument_fault_window(self):
        self._add_projects(1)
        self._enable_maintenance_avoidance()
        instrument = self.db.get(Instrument, 1)
        instrument.status = "fault"
        candidate = Task(
            project_id=1,
            name="方法开发",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
        )
        self.db.add(candidate)
        self.db.flush()
        self.db.add_all([
            InstrumentFault(
                instrument_id=instrument.id,
                reported_at=datetime(2026, 7, 13, 10, 0),
                estimated_resolved_at=datetime(2026, 7, 14, 10, 0),
                status="open",
            ),
            TimeSlot(
                task_id=candidate.id,
                instrument_id=instrument.id,
                plan_start=datetime(2026, 7, 14, 15, 0),
                plan_end=datetime(2026, 7, 14, 17, 0),
                status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(instrument.id, datetime(2026, 7, 13, 12, 0))

        moved_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == candidate.id,
            TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.id.desc()).first()
        self.assertIsNotNone(moved_slot)
        self.assertEqual(1, result["moved_tasks"])
        self.assertGreaterEqual(
            moved_slot.plan_start,
            datetime(2026, 7, 14, 10, 0),
        )

    def test_forward_shift_respects_resolved_instrument_fault_window(self):
        self._add_projects(1)
        self._enable_maintenance_avoidance()
        instrument = self.db.get(Instrument, 1)
        instrument.status = "idle"
        candidate = Task(
            project_id=1,
            name="方法开发",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
        )
        self.db.add(candidate)
        self.db.flush()
        self.db.add_all([
            InstrumentFault(
                instrument_id=instrument.id,
                reported_at=datetime(2026, 7, 13, 10, 0),
                estimated_resolved_at=datetime(2026, 7, 14, 9, 0),
                resolved_at=datetime(2026, 7, 14, 15, 30),
                status="resolved",
            ),
            TimeSlot(
                task_id=candidate.id,
                instrument_id=instrument.id,
                plan_start=datetime(2026, 7, 14, 16, 0),
                plan_end=datetime(2026, 7, 14, 18, 0),
                status="scheduled",
            ),
        ])
        self.db.commit()

        self._forward_shift(instrument.id, datetime(2026, 7, 13, 12, 0))

        moved_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == candidate.id,
            TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.id.desc()).first()
        self.assertIsNotNone(moved_slot)
        self.assertGreaterEqual(moved_slot.plan_start, datetime(2026, 7, 14, 15, 30))

    def test_forward_shift_does_not_cross_unfinished_predecessor(self):
        assignee = User(username="wang", display_name="王方", role="技术员")
        current = Task(
            project_id=1, name="当前报告", task_type="manual", status="completed",
            assignee=assignee, requires_human=True,
        )
        predecessor = Task(
            project_id=2, name="方法开发", task_type="test", status="scheduled",
            assignee=assignee, requires_human=True,
        )
        following = Task(
            project_id=2, name="方案撰写", task_type="manual", status="scheduled",
            assignee=assignee, requires_human=True,
        )
        self.db.add_all([assignee, current, predecessor, following])
        self.db.flush()
        self.db.add(TaskDependency(task_id=following.id, predecessor_id=predecessor.id))
        self.db.add_all([
            TimeSlot(
                task_id=predecessor.id, plan_start=datetime(2026, 7, 20, 14, 0),
                plan_end=datetime(2026, 7, 20, 16, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=following.id, plan_start=datetime(2026, 7, 20, 18, 0),
                plan_end=datetime(2026, 7, 20, 20, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._forward_shift(None, datetime(2026, 7, 20, 12, 0))

        slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following.id,
            TimeSlot.lifecycle_status == "active",
        ).one()
        self.assertEqual(datetime(2026, 7, 20, 18, 0), slot.plan_start)
        self.assertEqual(0, result["moved_tasks"])

    def _forward_shift(self, instrument_id: int, released_at: datetime) -> dict:
        working_options = {
            "day_start_minutes": 8 * 60 + 30,
            "day_end_minutes": 20 * 60,
            "include_weekends": True,
            "include_holidays": True,
            "horizon_end": datetime(2026, 7, 20),
            "calendar_days": {},
        }
        with patch(
            "app.services.schedule_completion_service._load_working_options",
            return_value=working_options,
        ):
            return _forward_shift_instrument_queue(self.db, instrument_id, released_at)



if __name__ == "__main__":
    unittest.main()

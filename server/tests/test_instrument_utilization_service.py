import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, InstrumentFault, Project, Task, TaskExecutionSegment, TaskNightRun, TimeSlot
from app.services.instrument_utilization_service import calculate_instrument_utilization


class InstrumentUtilizationServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.instrument = Instrument(code="ZBYY-002-0001", name="液质联用仪", availability_status="available")
        self.project = Project(code="XM-001", name="测试项目")
        self.db.add_all([self.instrument, self.project])
        self.db.flush()
        self.task = Task(
            project_id=self.project.id,
            name="检测任务",
            task_type="FFKF_001",
            status="done",
        )
        self.db.add(self.task)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def test_denominator_excludes_nights_and_weekends(self):
        slot = TimeSlot(
            task_id=self.task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 7, 31, 8, 30),
            plan_end=datetime(2026, 8, 3, 20, 0),
            actual_start=datetime(2026, 7, 31, 8, 30),
            actual_end=datetime(2026, 8, 3, 17, 0),
            status="completed",
        )
        self.db.add(slot)
        self.db.flush()
        self.db.add_all([
            TaskExecutionSegment(
                task_id=self.task.id,
                slot_id=slot.id,
                instrument_id=self.instrument.id,
                started_at=datetime(2026, 7, 31, 9, 0),
                ended_at=datetime(2026, 7, 31, 11, 0),
                end_reason="paused",
            ),
            TaskExecutionSegment(
                task_id=self.task.id,
                slot_id=slot.id,
                instrument_id=self.instrument.id,
                started_at=datetime(2026, 8, 3, 14, 0),
                ended_at=datetime(2026, 8, 3, 17, 0),
                end_reason="completed",
            ),
        ])
        self.db.commit()

        [result] = calculate_instrument_utilization(
            self.db,
            datetime(2026, 7, 31, 0, 0),
            datetime(2026, 8, 4, 0, 0),
        )

        self.assertEqual(96.0, result.total_available_hours)
        self.assertEqual(23.0, result.scheduled_hours)
        self.assertEqual(5.0, result.actual_run_hours)
        self.assertEqual(24.0, result.expected_utilization_rate)
        self.assertEqual(5.2, result.actual_utilization_rate)

    def test_actual_slot_duration_excludes_non_working_hours(self):
        self.db.add(TimeSlot(
            task_id=self.task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 7, 31, 8, 30),
            plan_end=datetime(2026, 8, 3, 20, 0),
            actual_start=datetime(2026, 7, 31, 8, 30),
            actual_end=datetime(2026, 8, 3, 17, 0),
            status="completed",
        ))
        self.db.commit()

        [result] = calculate_instrument_utilization(
            self.db,
            datetime(2026, 7, 31, 0, 0),
            datetime(2026, 8, 4, 0, 0),
        )

        self.assertEqual(20.0, result.actual_run_hours)
        self.assertEqual(20.8, result.actual_utilization_rate)

    def test_adds_registered_night_run_to_actual_hours(self):
        slot = TimeSlot(
            task_id=self.task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 7, 31, 19, 0),
            plan_end=datetime(2026, 7, 31, 22, 0),
            actual_start=datetime(2026, 7, 31, 19, 0),
            actual_end=datetime(2026, 7, 31, 22, 0),
            status="completed",
        )
        self.db.add(slot)
        self.db.flush()
        self.db.add_all([
            TaskExecutionSegment(
                task_id=self.task.id,
                slot_id=slot.id,
                instrument_id=self.instrument.id,
                started_at=datetime(2026, 7, 31, 19, 0),
                ended_at=datetime(2026, 7, 31, 22, 0),
                end_reason="completed",
            ),
            TaskNightRun(
                task_id=self.task.id,
                slot_id=slot.id,
                instrument_id=self.instrument.id,
                started_at=datetime(2026, 7, 31, 20, 0),
                ended_at=datetime(2026, 7, 31, 22, 0),
            ),
        ])
        self.db.commit()

        [result] = calculate_instrument_utilization(
            self.db,
            datetime(2026, 7, 31, 0, 0),
            datetime(2026, 8, 1, 0, 0),
        )

        self.assertEqual(3.0, result.actual_run_hours)
        self.assertEqual(12.5, result.actual_utilization_rate)

    def test_fault_is_removed_from_actual_slot_duration(self):
        slot = TimeSlot(
            task_id=self.task.id,
            instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 3, 8, 30),
            plan_end=datetime(2026, 8, 3, 20, 0),
            actual_start=datetime(2026, 8, 3, 8, 30),
            actual_end=datetime(2026, 8, 3, 20, 0),
            status="completed",
        )
        self.db.add(slot)
        self.db.flush()
        self.db.add_all([
            TaskExecutionSegment(
                task_id=self.task.id,
                slot_id=slot.id,
                instrument_id=self.instrument.id,
                started_at=datetime(2026, 8, 3, 8, 30),
                ended_at=datetime(2026, 8, 3, 20, 0),
                end_reason="completed",
            ),
            InstrumentFault(
                instrument_id=self.instrument.id,
                reported_at=datetime(2026, 8, 3, 12, 0),
                resolved_at=datetime(2026, 8, 3, 15, 0),
                status="resolved",
            ),
        ])
        self.db.commit()

        [result] = calculate_instrument_utilization(
            self.db,
            datetime(2026, 8, 3, 0, 0),
            datetime(2026, 8, 4, 0, 0),
        )

        self.assertEqual(8.5, result.actual_run_hours)
        self.assertEqual(35.4, result.actual_utilization_rate)

    def test_segmented_task_does_not_fallback_other_slots_to_full_actual_window(self):
        first_slot = TimeSlot(
            task_id=self.task.id, instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 17, 8, 30),
            plan_end=datetime(2026, 8, 17, 20, 0),
            actual_start=datetime(2026, 8, 17, 10, 0),
            actual_end=datetime(2026, 8, 17, 12, 0), status="completed",
        )
        continuation_slot = TimeSlot(
            task_id=self.task.id, instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 18, 8, 30),
            plan_end=datetime(2026, 8, 18, 20, 0),
            actual_start=datetime(2026, 8, 18, 8, 30),
            actual_end=datetime(2026, 8, 18, 20, 0), status="completed",
        )
        self.db.add_all([first_slot, continuation_slot])
        self.db.flush()
        self.db.add(TaskExecutionSegment(
            task_id=self.task.id, slot_id=first_slot.id,
            instrument_id=self.instrument.id,
            started_at=datetime(2026, 8, 17, 10, 0),
            ended_at=datetime(2026, 8, 17, 12, 0),
            end_reason="completed",
        ))
        self.db.commit()

        [result] = calculate_instrument_utilization(
            self.db, datetime(2026, 8, 17, 0, 0), datetime(2026, 8, 19, 0, 0),
        )

        self.assertEqual(2.0, result.actual_run_hours)


if __name__ == "__main__":
    unittest.main()

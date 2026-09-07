import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TaskExecutionSegment, TimeSlot
from app.services.execution_segment_lifecycle_service import (
    ExecutionSegmentStateError,
    close_open_execution_segment,
)
from scripts.repair_completed_open_segments import repair_completed_open_segments


class ExecutionSegmentLifecycleTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.project = Project(code="P-SEG", name="执行段测试项目")
        self.task = Task(
            project=self.project,
            name="稳定性检测",
            task_type="RCJC_001",
            status="running",
        )
        self.db.add(self.task)
        self.db.flush()
        self.slot = TimeSlot(
            task_id=self.task.id,
            plan_start=datetime(2026, 8, 31, 15, 30),
            plan_end=datetime(2026, 8, 31, 18, 0),
            actual_start=datetime(2026, 8, 31, 15, 30),
            actual_end=datetime(2026, 8, 31, 18, 0),
            status="completed",
        )
        self.db.add(self.slot)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def _add_open_segment(self, started_at: datetime) -> TaskExecutionSegment:
        segment = TaskExecutionSegment(
            task_id=self.task.id,
            slot_id=self.slot.id,
            started_at=started_at,
        )
        self.db.add(segment)
        self.db.flush()
        return segment

    def test_completion_closes_the_only_open_segment(self):
        segment = self._add_open_segment(datetime(2026, 8, 31, 15, 30))

        closed = close_open_execution_segment(
            self.task,
            datetime(2026, 8, 31, 18, 0),
            "completed",
        )

        self.assertEqual(segment.id, closed.id)
        self.assertEqual(datetime(2026, 8, 31, 18, 0), segment.ended_at)
        self.assertEqual("completed", segment.end_reason)

    def test_multiple_open_segments_fail_without_partial_close(self):
        first = self._add_open_segment(datetime(2026, 8, 31, 15, 30))
        second = self._add_open_segment(datetime(2026, 8, 31, 16, 0))

        with self.assertRaisesRegex(ExecutionSegmentStateError, "多条未结束"):
            close_open_execution_segment(
                self.task,
                datetime(2026, 8, 31, 18, 0),
                "completed",
            )

        self.assertIsNone(first.ended_at)
        self.assertIsNone(second.ended_at)

    def test_repair_uses_linked_slot_actual_end(self):
        self.task.status = "completed"
        segment = self._add_open_segment(datetime(2026, 8, 31, 15, 30))
        self.db.commit()

        preview = repair_completed_open_segments(self.db)

        self.assertEqual(datetime(2026, 8, 31, 18, 0), preview[0]["ended_at"])
        self.assertIsNone(segment.ended_at)

        repair_completed_open_segments(self.db, apply=True)
        self.db.refresh(segment)
        self.assertEqual(datetime(2026, 8, 31, 18, 0), segment.ended_at)
        self.assertEqual("slot_completed", segment.end_reason)


if __name__ == "__main__":
    unittest.main()

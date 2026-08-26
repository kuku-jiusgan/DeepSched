import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Task
from app.schemas.schemas import InstrumentCreate
from app.services.instrument_working_time_service import load_working_time_context
from app.services.schedule_forward_slot_service import build_forward_slots
from app.services.schedule_queue_replan_support import load_working_options


class InstrumentWorkingTimeServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.early = Instrument(
            code="EARLY", name="早班仪器",
            effective_work_start="08:30", effective_work_end="12:00",
        )
        self.late = Instrument(
            code="LATE", name="晚班仪器",
            effective_work_start="13:00", effective_work_end="20:00",
        )
        self.db.add_all([self.early, self.late])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_context_keeps_distinct_instrument_windows(self):
        context = load_working_time_context(
            self.db,
            datetime(2026, 8, 25, 0, 0),
            datetime(2026, 8, 26, 0, 0),
        )

        self.assertEqual(8 * 60 + 30, context.policy_for(self.early.id).day_start_minutes)
        self.assertEqual(13 * 60, context.policy_for(self.late.id).day_start_minutes)

    def test_forward_replan_uses_selected_instrument_window(self):
        task = Task(
            project_id=1, name="检测", task_type="test",
            requires_instrument=True, requires_human=False, allow_split=False,
        )
        self.db.add(task)
        self.db.commit()
        options = load_working_options(self.db, datetime(2026, 8, 25, 8, 30))

        ranges = build_forward_slots(
            self.db, task, self.late.id, 60,
            datetime(2026, 8, 25, 8, 30), options,
        )

        self.assertEqual(datetime(2026, 8, 25, 13, 0), ranges[0][0])
        self.assertEqual(datetime(2026, 8, 25, 14, 0), ranges[0][1])

    def test_instrument_schema_rejects_non_half_hour_window(self):
        with self.assertRaisesRegex(ValueError, "30 分钟刻度"):
            InstrumentCreate(
                code="INVALID", name="无效仪器",
                effective_work_start="08:15", effective_work_end="20:00",
            )


if __name__ == "__main__":
    unittest.main()

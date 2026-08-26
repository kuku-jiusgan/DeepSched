import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, InstrumentBridgeReservation, Project, Task, TimeSlot, User
from app.services.instrument_bridge_sync_service import (
    rebuild_instrument_bridge_reservations,
    valid_bridge_reservations,
)
from app.services.schedule_slot_change_log_service import supersede_slot


class InstrumentBridgeSyncServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        user = User(username="tech", display_name="技术员", role="技术员")
        project = Project(code="P-1", name="项目")
        instrument = Instrument(code="I-1", name="仪器")
        self.db.add_all([user, project, instrument])
        self.db.flush()
        previous = self._task(project.id, user.id, "前任务", True)
        manual = self._task(project.id, user.id, "方案撰写", False)
        following = self._task(project.id, user.id, "后任务", True)
        self.db.flush()
        self.previous_slot = self._slot(previous.id, instrument.id, 8, 10)
        self.manual_slot = self._slot(manual.id, None, 10, 11)
        self.following_slot = self._slot(following.id, instrument.id, 11, 13)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_rebuild_uses_only_current_active_slots(self):
        self.assertEqual(1, rebuild_instrument_bridge_reservations(self.db, "run-1"))
        reservation = self.db.query(InstrumentBridgeReservation).one()
        self.assertEqual(self.manual_slot.task_id, reservation.task_id)
        self.assertEqual(
            [reservation],
            valid_bridge_reservations(self.db, self.db.query(InstrumentBridgeReservation)),
        )

        supersede_slot(self.db, self.manual_slot, "排程重排")
        self.db.flush()

        self.assertEqual(0, self.db.query(InstrumentBridgeReservation).count())
        self.assertEqual(0, rebuild_instrument_bridge_reservations(self.db, "run-2"))

    def test_other_assignee_work_between_tasks_prevents_bridge(self):
        self.manual_slot.plan_start = datetime(2026, 8, 26, 11)
        self.manual_slot.plan_end = datetime(2026, 8, 26, 12)
        self.following_slot.plan_start = datetime(2026, 8, 26, 12)
        self.following_slot.plan_end = datetime(2026, 8, 26, 14)
        other = self._task(
            self.manual_slot.task.project_id,
            self.manual_slot.task.assignee_id,
            "中间工作",
            False,
        )
        self.db.flush()
        self._slot(other.id, None, 10, 11)
        self.db.commit()

        self.assertEqual(0, rebuild_instrument_bridge_reservations(self.db, "run-2"))

    def _task(self, project_id: int, assignee_id: int, name: str, instrument: bool) -> Task:
        task = Task(
            project_id=project_id,
            assignee_id=assignee_id,
            name=name,
            task_type="FFKF_001" if instrument else "QCFA_001",
            requires_instrument=instrument,
            requires_human=True,
            status="scheduled",
        )
        self.db.add(task)
        return task

    def _slot(self, task_id: int, instrument_id: int | None, start: int, end: int) -> TimeSlot:
        slot = TimeSlot(
            task_id=task_id,
            instrument_id=instrument_id,
            plan_start=datetime(2026, 8, 26, start),
            plan_end=datetime(2026, 8, 26, end),
            status="scheduled",
        )
        self.db.add(slot)
        return slot


if __name__ == "__main__":
    unittest.main()

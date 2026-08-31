import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.instrument_status_service import (
    delete_time_slots_and_refresh,
    effective_instrument_status,
)


class InstrumentStatusRefreshTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_deleting_last_running_slot_refreshes_instrument_to_idle(self):
        slot = self._create_running_slot()

        delete_time_slots_and_refresh(
            self.db,
            self.db.query(TimeSlot).filter(TimeSlot.id == slot.id),
        )

        instrument = self.db.query(Instrument).filter(Instrument.id == 1).one()
        self.assertEqual("idle", instrument.status)

    def test_deleting_one_running_slot_keeps_instrument_running_when_another_remains(self):
        first_slot = self._create_running_slot()
        second_task = Task(
            id=2,
            project_id=1,
            name="连续运行",
            task_type="TEST_002",
            status="running",
        )
        second_slot = TimeSlot(
            task_id=2,
            instrument_id=1,
            plan_start=datetime.now(),
            plan_end=datetime.now() + timedelta(hours=2),
            actual_start=datetime.now(),
            status="running",
        )
        self.db.add_all([second_task, second_slot])
        self.db.commit()

        delete_time_slots_and_refresh(
            self.db,
            self.db.query(TimeSlot).filter(TimeSlot.id == first_slot.id),
        )

        instrument = self.db.query(Instrument).filter(Instrument.id == 1).one()
        self.assertEqual("running", instrument.status)

    def test_superseded_running_slot_does_not_keep_instrument_running(self):
        """作废的时间槽不得让仪器一直显示运行中。

        暂停切换重排会把旧槽标记为 superseded，但不会清掉它的 running 状态。
        仪器状态如果只看 status，就会与首页（要求时间槽真的开始且未结束）
        给出相反的结论。
        """
        slot = self._create_running_slot()
        slot.lifecycle_status = "superseded"
        slot.superseded_reason = "暂停切换重排"
        self.db.commit()

        instrument = self.db.query(Instrument).filter(Instrument.id == 1).one()
        self.assertEqual("idle", effective_instrument_status(self.db, instrument))

    def test_finished_slot_does_not_keep_instrument_running(self):
        slot = self._create_running_slot()
        slot.actual_end = slot.actual_start + timedelta(minutes=30)
        self.db.commit()

        instrument = self.db.query(Instrument).filter(Instrument.id == 1).one()
        self.assertEqual("idle", effective_instrument_status(self.db, instrument))

    def test_genuinely_running_slot_keeps_instrument_running(self):
        self._create_running_slot()

        instrument = self.db.query(Instrument).filter(Instrument.id == 1).one()
        self.assertEqual("running", effective_instrument_status(self.db, instrument))

    def _create_running_slot(self) -> TimeSlot:
        now = datetime.now()
        project = Project(id=1, name="状态刷新项目", code="XM-STATUS")
        instrument = Instrument(
            id=1,
            code="ZBYY-002-0011",
            name="三重四极液质联用仪",
            status="running",
        )
        task = Task(
            id=1,
            project_id=1,
            name="方法开发",
            task_type="TEST_001",
            status="running",
        )
        slot = TimeSlot(
            id=1,
            task_id=1,
            instrument_id=1,
            plan_start=now,
            plan_end=now + timedelta(hours=1),
            actual_start=now,
            status="running",
        )
        self.db.add_all([project, instrument, task, slot])
        self.db.commit()
        return slot


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.instrument_status_service import effective_instrument_status
from app.services.schedule_slot_change_log_service import supersede_slot


class SupersedeSlotStatusTest(unittest.TestCase):
    """作废时间槽必须同时收掉它的执行状态。

    曾经 supersede_slot 只改生命周期，被它作废的槽会带着原来的 running /
    scheduled / paused 状态永远留在库里。任何忘记过滤生命周期的查询都会把这些
    废槽当成活的——仪器甘特图就因此让一台空闲仪器长期显示运行中。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        now = datetime.now()
        self.db.add_all([
            Project(id=1, name="作废状态项目", code="XM-SUPERSEDE"),
            Instrument(id=1, code="ZBYY-002-0002", name="三重四极气质联用仪", status="running"),
            Task(id=1, project_id=1, name="气质开发", task_type="TEST_001", status="scheduled"),
        ])
        self.slot = TimeSlot(
            id=1, task_id=1, instrument_id=1,
            plan_start=now, plan_end=now + timedelta(hours=1), status="running",
        )
        self.db.add(self.slot)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_superseding_resets_slot_status(self):
        supersede_slot(self.db, self.slot, "暂停切换重排")
        self.db.commit()

        self.assertEqual("superseded", self.slot.lifecycle_status)
        self.assertEqual("cancelled", self.slot.status)

    def test_superseded_slot_no_longer_marks_instrument_running(self):
        supersede_slot(self.db, self.slot, "暂停切换重排")
        self.db.commit()

        instrument = self.db.query(Instrument).filter(Instrument.id == 1).one()
        self.assertEqual("idle", effective_instrument_status(self.db, instrument))


if __name__ == "__main__":
    unittest.main()

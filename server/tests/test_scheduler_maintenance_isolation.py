import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, InstrumentFault, MaintenanceWindow, Project, Task, TimeSlot
from app.services.scheduler import SchedulerService


class SchedulerMaintenanceIsolationTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        # 排程只落在工作日内。基准日必须跳过周末，否则周五、周六运行时
        # "明天"落在休息日，排程会被推到下周一，断言随之失败。
        start = (datetime.now() + timedelta(days=1)).replace(
            hour=8, minute=30, second=0, microsecond=0,
        )
        while start.weekday() >= 5:
            start += timedelta(days=1)
        self.horizon_start = start

    def tearDown(self):
        self.db.close()

    def test_maintenance_only_blocks_its_own_instrument(self):
        project = Project(
            name="维护隔离测试",
            code="MAINT-ISOLATION",
            estimated_hours=10,
            start_date=self.horizon_start,
            end_date=self.horizon_start + timedelta(days=5),
        )
        maintained = Instrument(
            code="MAINT-A",
            name="维护仪器A",
            availability_status="available",
            status="idle",
        )
        available = Instrument(
            code="MAINT-B",
            name="可用仪器B",
            availability_status="available",
            status="idle",
        )
        self.db.add_all([project, maintained, available])
        self.db.flush()
        self.db.add(MaintenanceWindow(
            instrument_id=maintained.id,
            start_time=self.horizon_start,
            end_time=self.horizon_start + timedelta(hours=4),
            mw_type="maintenance",
        ))
        task = Task(
            project_id=project.id,
            name="指定B仪器任务",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=1,
            instrument_ids=[available.id],
            status="pending",
        )
        self.db.add(task)
        self.db.flush()

        total_units = 10 * 48
        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(
                self.horizon_start,
                self.horizon_start + timedelta(days=10),
                total_units,
            ),
        ):
            result = SchedulerService(self.db).generate(
                project_ids=[project.id],
                current_project_id=project.id,
                commit=False,
            )

        slot = self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).one()
        self.assertEqual("ok", result["status"])
        self.assertEqual(available.id, slot.instrument_id)
        self.assertEqual(self.horizon_start, slot.plan_start)

    def test_faulted_instrument_can_schedule_after_estimated_repair(self):
        project = Project(
            name="故障仪器预计维修后排程",
            code="FAULT-REPAIR-SCHEDULE",
            estimated_hours=1,
            start_date=self.horizon_start,
            end_date=self.horizon_start + timedelta(days=2),
        )
        instrument = Instrument(
            code="ZBYY-002-0007",
            name="三重四极气质联用仪",
            availability_status="available",
            status="fault",
        )
        self.db.add_all([project, instrument])
        self.db.flush()
        estimated_resolved_at = self.horizon_start + timedelta(hours=1)
        self.db.add(InstrumentFault(
            instrument_id=instrument.id,
            reported_at=self.horizon_start - timedelta(hours=23),
            estimated_resolved_at=estimated_resolved_at,
            status="open",
            description="SIM模式和RMR模式不出峰",
        ))
        task = Task(
            project_id=project.id,
            name="方法验证",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=1,
            instrument_ids=[instrument.id],
            status="pending",
        )
        self.db.add(task)
        self.db.flush()

        total_units = 10 * 48
        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(
                self.horizon_start,
                self.horizon_start + timedelta(days=10),
                total_units,
            ),
        ):
            result = SchedulerService(self.db).generate(
                project_ids=[project.id],
                current_project_id=project.id,
                commit=False,
            )

        slot = self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).one()
        self.assertEqual("ok", result["status"])
        self.assertEqual(instrument.id, slot.instrument_id)
        self.assertGreaterEqual(slot.plan_start, estimated_resolved_at)

    def test_faulted_instrument_without_repair_time_is_not_scheduled(self):
        project = Project(
            name="无预计维修故障仪器",
            code="FAULT-NO-ETA",
            estimated_hours=1,
            start_date=self.horizon_start,
            end_date=self.horizon_start + timedelta(days=2),
        )
        instrument = Instrument(
            code="FAULT-NO-ETA-INST",
            name="无预计维修仪器",
            availability_status="available",
            status="fault",
        )
        task = Task(
            project=project,
            name="方法验证",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=1,
            instrument_ids=[],
            status="pending",
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()

        total_units = 10 * 48
        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(
                self.horizon_start,
                self.horizon_start + timedelta(days=10),
                total_units,
            ),
        ):
            result = SchedulerService(self.db).generate(
                project_ids=[project.id],
                current_project_id=project.id,
                commit=False,
            )

        self.assertEqual("error", result["status"])
        self.assertFalse(self.db.query(TimeSlot).filter(TimeSlot.task_id == task.id).first())


if __name__ == "__main__":
    unittest.main()

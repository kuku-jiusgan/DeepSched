import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    AuditLog,
    Instrument,
    Project,
    Task,
    TaskExecutionSegment,
    TimeSlot,
    User,
)
from app.services.schedule_completion_service import complete_task_and_shift


def _base_day() -> datetime:
    """用例的基准日：下一个周一零点。

    两条约束叠在一起才定得下来：

    整套时间必须落在真实当前时刻之后——写死日期会随时间推移变成过去，而排程不会
    往回排，被前移的任务无处可去，moved_tasks 恒为 0。用例只冻结了执行服务的当前
    时刻，求解器的时间视界用的仍是真实时间，所以数据本身也必须放在未来。

    基准日、+2 天、+3 天还必须都是工作日。用例给完成服务patch了 24 小时全天候的
    工作时段，但求解器读的是真实工作日历（8:30-20:00、不含周末），任何一天落到
    周末，被前移的任务就会被推到下周一，时间断言随之失败。取周一即可让这三天
    落在周一、周三、周四。
    """
    day = (datetime.now() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    while day.weekday() != 0:
        day += timedelta(days=1)
    return day


BASE_DAY = _base_day()


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = BASE_DAY.replace(hour=13)
        return value if tz is None else value.replace(tzinfo=tz)


class ScheduleResumeQueueReplanTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()

    def tearDown(self):
        self.db.close()

    def test_early_completion_resumes_source_and_replans_following_queue(self):
        operator = User(username="tech", display_name="技术员", role="技术员")
        instrument = Instrument(code="LCMS-01", name="液质联用仪")
        source_project = Project(code="DETECT", name="样品检测")
        target_project = Project(code="RESEARCH", name="研究项目")
        following_project = Project(code="NEXT", name="后续项目")
        self.db.add_all([
            operator, instrument, source_project, target_project, following_project,
        ])
        self.db.flush()

        source = Task(
            project=source_project, name="样品检测", task_type="test",
            status="paused", est_duration_hours=1, requires_instrument=True,
            requires_human=True, assignee=operator,
        )
        target = Task(
            project=target_project, name="方法开发", task_type="test",
            status="running", requires_instrument=True, requires_human=True,
            assignee=operator,
        )
        following = Task(
            project=following_project, name="后续方法开发", task_type="test",
            # 工时必须是 30 分钟的整数倍：排程颗粒度就是 30 分钟，1.75 小时会被
            # to_units 向上取整成 2 小时，用例断言的 105 分钟在网格上表示不出来。
            status="scheduled", est_duration_hours=2, requires_instrument=True,
            requires_human=True, assignee=operator,
        )
        self.db.add_all([source, target, following])
        self.db.flush()

        old_source_slot = TimeSlot(
            task=source, instrument_id=instrument.id,
            plan_start=BASE_DAY.replace(hour=12, minute=0),
            plan_end=BASE_DAY.replace(hour=12, minute=30),
            actual_start=BASE_DAY.replace(hour=12, minute=0),
            actual_end=BASE_DAY.replace(hour=12, minute=30), status="paused",
        )
        recovery_slot = TimeSlot(
            task=source, instrument_id=instrument.id,
            plan_start=BASE_DAY.replace(hour=9, minute=30) + timedelta(days=2),
            plan_end=BASE_DAY.replace(hour=10, minute=30) + timedelta(days=2), status="paused",
        )
        target_slot = TimeSlot(
            task=target, instrument_id=instrument.id,
            plan_start=BASE_DAY.replace(hour=12, minute=30),
            plan_end=BASE_DAY.replace(hour=9, minute=30) + timedelta(days=2),
            actual_start=BASE_DAY.replace(hour=12, minute=30), status="running",
        )
        following_slot = TimeSlot(
            task=following, instrument_id=instrument.id,
            plan_start=BASE_DAY.replace(hour=10, minute=30) + timedelta(days=2),
            plan_end=BASE_DAY.replace(hour=12, minute=30) + timedelta(days=2), status="scheduled",
        )
        stale_slot = TimeSlot(
            task=following, instrument_id=instrument.id,
            plan_start=BASE_DAY.replace(hour=8, minute=30) + timedelta(days=3),
            plan_end=BASE_DAY.replace(hour=18, minute=30) + timedelta(days=3), status="scheduled",
            lifecycle_status="superseded", superseded_reason="历史重排",
        )
        self.db.add_all([
            old_source_slot, recovery_slot, target_slot, following_slot, stale_slot,
        ])
        self.db.flush()
        self.db.add_all([
            TaskExecutionSegment(
                task_id=target.id, slot_id=target_slot.id,
                instrument_id=instrument.id,
                started_at=BASE_DAY.replace(hour=12, minute=30),
            ),
            AuditLog(
                user_name="技术员", action="task_paused", target_type="task",
                target_id=source.id,
                detail={
                    "source_task_id": source.id,
                    "source_slot_id": old_source_slot.id,
                    "target_task_id": target.id,
                    "target_slot_id": target_slot.id,
                },
            ),
        ])
        self.db.commit()

        working_options = {
            "day_start_minutes": 0,
            "day_end_minutes": 24 * 60,
            "include_weekends": True,
            "include_holidays": True,
            "horizon_end": BASE_DAY + timedelta(days=40),
            "calendar_days": {},
        }
        with patch("app.services.task_execution_service.datetime", FixedDatetime), patch(
            "app.services.schedule_completion_service._load_working_options",
            return_value=working_options,
        ):
            result = complete_task_and_shift(
                self.db, target.id,
                actual_end_time=FixedDatetime.now(),
                completed_slot_id=target_slot.id,
                release_instrument=True,
            )
        self.db.flush()

        active_following = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following.id,
            TimeSlot.lifecycle_status == "active",
        ).one()
        self.assertEqual(source.id, result["resumed_task_id"])
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(FixedDatetime.now(), recovery_slot.plan_start)
        self.assertEqual(
            recovery_slot.plan_end + timedelta(minutes=30),
            active_following.plan_start,
        )
        self.assertEqual(
            120,
            int((active_following.plan_end - active_following.plan_start).total_seconds() / 60),
        )
        self.assertEqual("superseded", following_slot.lifecycle_status)
        self.assertEqual("superseded", stale_slot.lifecycle_status)


if __name__ == "__main__":
    unittest.main()

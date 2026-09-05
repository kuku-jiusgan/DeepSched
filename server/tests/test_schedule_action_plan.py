"""求解结果先变成一份计划，再由既有落盘服务执行。

原先"要往库里写什么"和"怎么写"是揉在一起的：persist_slots 一边从求解器读值、一边
db.add、一边改任务状态、一边重建桥接。于是这份结果没法先看一眼再决定要不要采纳——
探测只能靠 savepoint 包住整段写操作再回滚。

拆开之后，计划是一个纯值：可以检查、可以丢弃、可以序列化后交给另一个进程。
"""

import pickle
import unittest
import unittest.mock
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.schedule_action_plan import (
    CreateSlot,
    NotifySchedule,
    SchedulePlan,
    SetTaskStatus,
    SupersedeSlot,
    build_schedule_plan,
)


class _Solver:
    def __init__(self, values):
        self.values = values

    def Value(self, key):
        return self.values[key]


def _task(task_id, **kwargs):
    return SimpleNamespace(
        id=task_id, name="任务%d" % task_id, status=kwargs.get("status", "pending"),
        requires_instrument=kwargs.get("requires_instrument", False),
        allow_split=kwargs.get("allow_split", False),
    )


class SchedulePlanIsAValueTest(unittest.TestCase):
    def test_plan_is_immutable_and_serializable(self):
        plan = SchedulePlan(
            schedule_run_id="run-1",
            frozen_boundary=datetime(2026, 9, 6),
            confirmed_boundary=datetime(2026, 9, 12),
            slots=(CreateSlot(1, 10, datetime(2026, 9, 7, 8, 30),
                              datetime(2026, 9, 7, 12, 0), "scheduled"),),
            supersedes=(SupersedeSlot(7, "CP-SAT局部重排"),),
            task_statuses=(SetTaskStatus(1, "scheduled"),),
        )

        self.assertEqual(plan, pickle.loads(pickle.dumps(plan)))
        with self.assertRaises(Exception):
            plan.schedule_run_id = "run-2"


class BuildScheduleplanTouchesNothingTest(unittest.TestCase):
    """算计划的过程只读求解器取值，不碰数据库、不改任何对象。"""

    def setUp(self):
        self.horizon_start = datetime(2026, 9, 7, 8, 30)
        self.working_context = SimpleNamespace(
            policy_for=lambda instrument_id: SimpleNamespace(
                day_start_minutes=8 * 60 + 30, day_end_minutes=20 * 60,
                include_weekends=True, include_holidays=True,
            ),
            calendar_days={},
        )

    def _build(self, tasks, **kwargs):
        defaults = dict(
            tasks=tasks, instruments=[], solver=_Solver(kwargs.pop("values", {})),
            task_starts=kwargs.pop("task_starts", {}),
            task_ends=kwargs.pop("task_ends", {}),
            presences={}, split_unit_presences={},
            horizon_start=self.horizon_start, working_context=self.working_context,
            schedule_run_id="run-1", supersedes=(), notify=None, base_epoch=0,
            frozen_boundary=self.horizon_start - timedelta(days=1),
            confirmed_boundary=self.horizon_start + timedelta(days=7),
            forecast_task_ids=set(), preserved_status_task_ids=set(),
            immovable_statuses={"running"}, preserved_statuses={"paused", "interrupted"},
        )
        defaults.update(kwargs)
        return build_schedule_plan(**defaults)

    def test_a_plain_task_becomes_one_slot_and_one_status_change(self):
        task = _task(1)
        plan = self._build(
            [task], values={"s": 0, "e": 4}, task_starts={1: "s"}, task_ends={1: "e"},
        )

        self.assertEqual(1, len(plan.slots))
        self.assertEqual(self.horizon_start, plan.slots[0].plan_start)
        self.assertEqual(self.horizon_start + timedelta(hours=2), plan.slots[0].plan_end)
        self.assertEqual((SetTaskStatus(1, "scheduled"),), plan.task_statuses)
        # 任务对象本身不能被改动——改状态是执行阶段的事。
        self.assertEqual("pending", task.status)

    def test_a_paused_task_keeps_its_status(self):
        """暂停任务的位置可以被重排，但状态不能被改写成待执行。"""
        plan = self._build(
            [_task(1, status="paused")],
            values={"s": 0, "e": 2}, task_starts={1: "s"}, task_ends={1: "e"},
        )

        self.assertEqual("paused", plan.slots[0].status)
        self.assertEqual((), plan.task_statuses)

    def test_a_running_task_is_left_alone(self):
        plan = self._build(
            [_task(1, status="running")],
            values={"s": 0, "e": 2}, task_starts={1: "s"}, task_ends={1: "e"},
        )

        self.assertEqual((), plan.slots)
        self.assertEqual((), plan.task_statuses)

    def test_an_unapproved_downstream_task_gets_no_slot(self):
        """未签批方案的下游任务只占产能，不落地时间槽。"""
        plan = self._build(
            [_task(1)], values={"s": 0, "e": 2}, task_starts={1: "s"},
            task_ends={1: "e"}, forecast_task_ids={1},
        )

        self.assertEqual((), plan.slots)


class SupersedeThenCreateTest(unittest.TestCase):
    """一份计划里，作废必须排在新建前面。

    建槽时会按 任务/仪器/起止/状态 去重。旧槽还没作废就去建新槽，本该新建的那一条
    会被判成重复而跳过，于是任务的时间槽凭空少一段。顺序在 SchedulePlan 里是有
    语义的，不是随手排的。
    """

    def test_the_old_slot_is_voided_before_the_new_one_is_created(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.core.database import Base
        from app.models import Project, Task, TimeSlot
        from app.services.schedule_action_plan import apply_schedule_plan

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            start = datetime(2026, 9, 7, 8, 30)
            project = Project(code="SUP-1", name="作废项目", priority=1,
                              start_date=start, end_date=start + timedelta(days=10))
            db.add(project)
            db.flush()
            task = Task(project_id=project.id, name="作废任务", task_type="test",
                        status="pending", est_duration_hours=1,
                        requires_instrument=False, requires_human=False)
            db.add(task)
            db.flush()
            old = TimeSlot(
                task_id=task.id, schedule_run_id="run-0", instrument_id=None,
                plan_start=start, plan_end=start + timedelta(hours=2),
                tier="confirmed", status="scheduled", lifecycle_status="active",
            )
            db.add(old)
            db.flush()

            # 新槽与旧槽的任务、起止、状态完全相同——去重规则会命中。
            created = apply_schedule_plan(db, SchedulePlan(
                schedule_run_id="run-1",
                frozen_boundary=start - timedelta(days=1),
                confirmed_boundary=start + timedelta(days=7),
                supersedes=(SupersedeSlot(old.id, "CP-SAT局部重排"),),
                slots=(CreateSlot(task.id, None, start,
                                  start + timedelta(hours=2), "scheduled"),),
                task_statuses=(SetTaskStatus(task.id, "scheduled"),),
            ))
            db.flush()

            self.assertEqual(1, created, "旧槽先作废之后，新槽必须能建出来")
            self.assertEqual("superseded", old.lifecycle_status)
            self.assertEqual("cancelled", old.status)
            active = db.query(TimeSlot).filter(
                TimeSlot.lifecycle_status == "active",
            ).all()
            self.assertEqual(1, len(active))
            self.assertEqual("run-1", active[0].schedule_run_id)
            self.assertEqual("scheduled", task.status)
        finally:
            db.close()


class NotificationIsAnOutwardActionTest(unittest.TestCase):
    """通知是整份计划里唯一对外可见的动作，发出去就收不回来。

    它必须排在一致性校验通过之后：校验不过会整体回滚，而已经推送给人的消息回滚
    不掉。所以写库动作走 apply_schedule_plan，通知单独走
    apply_schedule_notifications，两者之间隔着那道闸。

    探测跑的计划 notify 恒为 None——试排不该惊动任何人。
    """

    def _plan(self, notify):
        return SchedulePlan(
            schedule_run_id="run-1",
            frozen_boundary=datetime(2026, 9, 6),
            confirmed_boundary=datetime(2026, 9, 12),
            notify=notify,
        )

    def test_applying_the_write_actions_never_notifies(self):
        from unittest.mock import patch

        from app.services.schedule_action_plan import apply_schedule_plan

        with patch(
            "app.services.schedule_advance_notification_service"
            ".notify_rescheduled_tasks_advanced",
        ) as advanced, patch(
            "app.services.instrument_bridge_sync_service"
            ".rebuild_instrument_bridge_reservations",
        ), patch(
            # 版本号占用不是这条测试要验的东西，这里的 db 是个替身。
            "app.services.schedule_action_plan.claim",
        ):
            apply_schedule_plan(
                unittest.mock.MagicMock(),
                self._plan(NotifySchedule("重新排程", {1: (None, None)})),
            )

        advanced.assert_not_called()

    def test_a_plan_without_notification_sends_nothing(self):
        from unittest.mock import patch

        from app.services.schedule_action_plan import apply_schedule_notifications

        with patch(
            "app.services.schedule_advance_notification_service"
            ".notify_rescheduled_tasks_advanced",
        ) as advanced:
            apply_schedule_notifications(unittest.mock.MagicMock(), self._plan(None))

        advanced.assert_not_called()

    def test_a_plan_with_notification_sends_both_directions(self):
        from unittest.mock import patch

        from app.services.schedule_action_plan import apply_schedule_notifications

        plan = self._plan(NotifySchedule("仪器故障重排", {7: (None, None)}))
        with patch(
            "app.services.schedule_advance_notification_service"
            ".notify_rescheduled_tasks_advanced",
        ) as advanced, patch(
            "app.services.schedule_advance_notification_service"
            ".notify_rescheduled_tasks_delayed",
        ) as delayed:
            apply_schedule_notifications(unittest.mock.MagicMock(), plan)

        self.assertEqual("仪器故障重排", advanced.call_args[0][2])
        self.assertEqual({7: (None, None)}, delayed.call_args[0][1])


if __name__ == "__main__":
    unittest.main()

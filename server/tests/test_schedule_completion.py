import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    AuditLog,
    Instrument,
    Notification,
    Project,
    Task,
    TimeSlot,
    User,
)
from app.services.schedule_completion_service import (
    _paused_switch_source_hint,
    _mark_task_slots_completed,
    _select_completed_slot,
    complete_task_and_shift,
)


class ScheduleCompletionTest(unittest.TestCase):
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

    def test_complete_rejects_already_completed_task(self):
        task = Task(project_id=1, name="done", task_type="test", status="completed")
        self.db.add(task)
        self.db.flush()
        self.db.add(TimeSlot(task_id=task.id, instrument_id=None, plan_start=datetime(2026, 7, 20, 8, 30), plan_end=datetime(2026, 7, 20, 10, 30), status="completed"))
        self.db.commit()
        result = complete_task_and_shift(self.db, task.id)
        self.assertEqual("error", result["status"])
        self.assertIn("已经完成", result["message"])

    def test_completing_the_switch_target_does_not_restart_the_paused_task(self):
        """接替任务完成后不替人开工，只提示原任务还停着。

        暂停切换会把接替任务的连续后续任务一起排进队列，接替任务一完成就自动
        恢复原任务，等于跳过了那些还没开始的后续任务。
        """
        source = Task(project_id=1, name="方法验证", task_type="test", status="paused")
        target = Task(project_id=1, name="NDMA检测", task_type="test", status="running")
        self.db.add_all([source, target])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=source.id, instrument_id=1,
                plan_start=datetime(2026, 7, 21, 8, 30),
                plan_end=datetime(2026, 7, 21, 12, 0),
                status="paused",
            ),
            TimeSlot(
                task_id=target.id, instrument_id=1,
                plan_start=datetime(2026, 7, 20, 8, 30),
                plan_end=datetime(2026, 7, 20, 10, 30),
                actual_start=datetime(2026, 7, 20, 8, 30), status="running",
            ),
            AuditLog(
                user_name="王福芳", action="task_paused", target_type="task",
                target_id=source.id,
                detail={"source_task_id": source.id, "target_task_id": target.id},
            ),
        ])
        self.db.commit()

        with patch(
            "app.services.schedule_completion_service._forward_shift_instrument_queue",
            return_value={"status": "ok", "message": "任务已完成", "moved_tasks": 0},
        ):
            result = complete_task_and_shift(
                self.db, target.id, actual_end_time=datetime(2026, 7, 20, 10, 0),
            )

        self.db.refresh(source)
        self.assertEqual("ok", result["status"])
        self.assertEqual("paused", source.status)
        self.assertIn("方法验证", result["message"])
        self.assertIn("仍处于暂停", result["message"])

    def test_paused_switch_source_hint_ignores_already_resumed_task(self):
        source = Task(project_id=1, name="方法验证", task_type="test", status="running")
        target = Task(project_id=1, name="NDMA检测", task_type="test", status="completed")
        self.db.add_all([source, target])
        self.db.flush()
        self.db.add(AuditLog(
            user_name="王福芳", action="task_paused", target_type="task",
            target_id=source.id,
            detail={"source_task_id": source.id, "target_task_id": target.id},
        ))
        self.db.commit()

        self.assertIsNone(_paused_switch_source_hint(self.db, target.id))

    def test_complete_succeeds_when_paused_source_cannot_resume(self):
        task = Task(project_id=1, name="current", task_type="test", status="running")
        self.db.add(task)
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 20, 8, 30),
            plan_end=datetime(2026, 7, 20, 10, 30),
            actual_start=datetime(2026, 7, 20, 8, 30), status="running",
        )
        self.db.add(slot)
        self.db.commit()

        with patch(
            "app.services.schedule_completion_service._paused_switch_source_hint",
            return_value="原暂停任务【source】仍处于暂停，请在工作台上手动继续",
        ), patch(
            "app.services.schedule_completion_service._forward_shift_instrument_queue",
            return_value={"status": "ok", "message": "任务已完成，后续队列无需调整", "moved_tasks": 0},
        ):
            result = complete_task_and_shift(
                self.db, task.id, actual_end_time=datetime(2026, 7, 20, 10, 0),
            )

        self.assertEqual("ok", result["status"])
        self.assertEqual("completed", task.status)
        self.assertIn("原暂停任务【source】仍处于暂停", result["message"])

    def test_early_completion_uses_resource_queue_not_project_reschedule(self):
        task = Task(
            project_id=1, name="方法验证", task_type="test", status="running",
            assignee_id=7, requires_instrument=True, requires_human=True,
        )
        self.db.add(task)
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 20, 8, 30),
            plan_end=datetime(2026, 7, 20, 12, 30),
            actual_start=datetime(2026, 7, 20, 8, 30), status="running",
        )
        self.db.add(slot)
        self.db.commit()

        with patch(
            "app.services.schedule_completion_service._forward_shift_instrument_queue",
            return_value={
                "status": "ok", "message": "任务已完成，该仪器跨项目前移 1 个任务",
                "moved_tasks": 1, "moved_task_details": [],
            },
        ) as replan_queue:
            result = complete_task_and_shift(
                self.db, task.id,
                actual_end_time=datetime(2026, 7, 20, 10, 0),
            )

        self.assertEqual("ok", result["status"])
        replan_queue.assert_called_once_with(
            self.db, 1, datetime(2026, 7, 20, 10, 0), 7, 1,
        )

    def test_complete_multi_day_task_preserves_plan_boundaries(self):
        task = Task(project_id=1, name="multi-day", task_type="test", status="running")
        self.db.add(task)
        self.db.flush()
        slots = [
            TimeSlot(
                task_id=task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 10, 17, 30),
                plan_end=datetime(2026, 7, 10, 20, 0), status="running",
            ),
            TimeSlot(
                task_id=task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 11, 8, 30),
                plan_end=datetime(2026, 7, 11, 20, 0), status="scheduled",
            ),
            TimeSlot(
                task_id=task.id, instrument_id=1,
                plan_start=datetime(2026, 7, 12, 8, 30),
                plan_end=datetime(2026, 7, 12, 17, 0), status="scheduled",
            ),
        ]
        self.db.add_all(slots)
        self.db.commit()
        original_ranges = [(slot.plan_start, slot.plan_end) for slot in slots]
        end_time = datetime(2026, 7, 13, 9, 23)

        completed_slot = _select_completed_slot(slots, slots[0].id, end_time)
        _mark_task_slots_completed(self.db, slots, completed_slot, end_time)

        self.assertEqual(slots[-1].id, completed_slot.id)
        self.assertEqual(original_ranges, [(slot.plan_start, slot.plan_end) for slot in slots])
        self.assertTrue(all(slot.status == "completed" for slot in slots))
        self.assertEqual(end_time, slots[-1].actual_end)
        self.assertEqual(datetime(2026, 7, 10, 20, 0), slots[0].actual_end)
        self.assertEqual(datetime(2026, 7, 11, 20, 0), slots[1].actual_end)

    def test_future_unexecuted_segments_are_superseded(self):
        slots = [
            TimeSlot(
                id=1, task_id=1, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 20, 0), status="running",
            ),
            TimeSlot(
                id=2, task_id=1, instrument_id=1,
                plan_start=datetime(2026, 7, 14, 8, 30),
                plan_end=datetime(2026, 7, 14, 18, 30), status="scheduled",
            ),
        ]
        self.db.add_all(slots)
        self.db.commit()
        end_time = datetime(2026, 7, 13, 10, 0)

        _mark_task_slots_completed(self.db, slots, slots[0], end_time)
        self.db.flush()

        remaining = self.db.query(TimeSlot).filter(TimeSlot.task_id == 1).all()
        self.assertEqual([1, 2], [slot.id for slot in remaining])
        self.assertEqual("completed", remaining[0].status)
        self.assertEqual(end_time, remaining[0].actual_end)
        self.assertEqual("cancelled", remaining[1].status)
        self.assertEqual("superseded", remaining[1].lifecycle_status)

    def test_completion_refreshes_bridge_reservations_before_queue_replan(self):
        task = Task(project_id=1, name="bridge-source", task_type="test", status="running")
        self.db.add(task)
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id, instrument_id=1,
            plan_start=datetime(2026, 7, 13, 8, 30),
            plan_end=datetime(2026, 7, 13, 20, 0),
            actual_start=datetime(2026, 7, 13, 8, 30), status="running",
        ))
        self.db.commit()

        with patch(
            "app.services.schedule_completion_service.rebuild_instrument_bridge_reservations",
        ) as rebuild, patch(
            "app.services.schedule_completion_service._forward_shift_instrument_queue",
            return_value={"status": "ok", "message": "后续队列无需调整", "moved_tasks": 0},
        ):
            result = complete_task_and_shift(
                self.db, task.id, actual_end_time=datetime(2026, 7, 13, 10, 0),
            )

        self.assertEqual("ok", result["status"])
        rebuild.assert_called_once_with(self.db)

    def test_early_completion_notifies_each_moved_task_assignee(self):
        assignee = User(
            username="analyst",
            display_name="任务负责人",
            role="分析员",
            is_active=True,
        )
        completed = Task(project_id=1, name="前序检测", task_type="test", status="running")
        moved = Task(
            project_id=2,
            name="后续检测",
            task_type="test",
            status="scheduled",
            requires_human=False,
            assignee=assignee,
        )
        self.db.add_all([assignee, completed, moved])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 14, 0), status="running",
            ),
            TimeSlot(
                task_id=moved.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 15, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 12, 0))

        notifications = self.db.query(Notification).order_by(Notification.id).all()
        notification = next(item for item in notifications if item.channel == "site")
        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(["site", "wecom"], [item.channel for item in notifications])
        self.assertEqual("analyst", notification.user_name)
        self.assertEqual("task_schedule_advanced", notification.n_type)
        self.assertEqual("任务前移通知", notification.title)
        self.assertIn("新时间：7/13（周一）12:30–14:30（2小时）", notification.content)
        self.assertIn("原时间：7/13 15:00–17:00（已提前）", notification.content)
        self.assertIn("原因：前序任务“前序检测”今日已提前完成。", notification.content)

    def test_on_time_completion_does_not_send_advance_notification(self):
        assignee = User(
            username="analyst",
            display_name="任务负责人",
            role="分析员",
            is_active=True,
        )
        completed = Task(project_id=1, name="前序检测", task_type="test", status="running")
        moved = Task(
            project_id=2,
            name="后续检测",
            task_type="test",
            status="scheduled",
            requires_human=False,
            assignee=assignee,
        )
        self.db.add_all([assignee, completed, moved])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 14, 0), status="running",
            ),
            TimeSlot(
                task_id=moved.id, instrument_id=1,
                plan_start=datetime(2026, 7, 13, 15, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 14, 0))

        self.assertEqual(1, result["moved_tasks"])
        self.assertEqual(0, self.db.query(Notification).count())

    def test_reported_late_completion_shifts_following_task_for_same_assignee(self):
        project = Project(
            id=1, name="延期项目", code="DELAY-1",
            start_date=datetime(2026, 7, 13),
            end_date=datetime(2026, 7, 20, 23, 59),
        )
        assignee = User(
            id=7, username="analyst-7", display_name="负责人",
            role="分析员", is_active=True,
        )
        completed = Task(
            project=project, name="方法验证", task_type="test", status="running",
            requires_human=True, assignee=assignee,
        )
        following = Task(
            project_id=2, name="报告撰写", task_type="manual", status="scheduled",
            delay_status="delayed", requires_human=True, assignee=assignee,
        )
        self.db.add_all([
            project,
            Project(
                id=2, name="后续项目", code="DELAY-2",
                start_date=datetime(2026, 7, 13),
                end_date=datetime(2026, 7, 20, 23, 59),
            ),
            assignee, completed, following,
        ])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 9, 0), status="running",
            ),
            TimeSlot(
                task_id=following.id, plan_start=datetime(2026, 7, 13, 9, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.add(AuditLog(
            user_name="operator", action="task_delay_reported",
            target_type="time_slot", target_id=completed.time_slots[0].id,
            detail={"task_id": completed.id, "delay_hours": 1, "reason": "实验延期"},
        ))
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 10, 27))

        shifted = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following.id, TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.id.desc()).first()
        self.assertIsNotNone(shifted)
        self.assertEqual(datetime(2026, 7, 13, 10, 30), shifted.plan_start)
        self.assertEqual(datetime(2026, 7, 13, 18, 30), shifted.plan_end)
        self.db.refresh(following)
        self.assertEqual("delayed", following.delay_status)
        self.assertEqual(1, result["delay_affected_tasks"])
        self.assertEqual(0, result["moved_tasks"])

    def test_reported_late_completion_keeps_delay_on_blocked_following_task(self):
        project = Project(
            id=1, name="延期项目", code="DELAY-BLOCKED",
            end_date=datetime(2026, 7, 20, 23, 59),
        )
        completed = Task(
            project=project, name="方法验证", task_type="test", status="running",
        )
        following = Task(
            project=project, name="报告撰写", task_type="manual", status="blocked",
            delay_status="delayed",
        )
        self.db.add_all([project, completed, following])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 9, 0), status="running",
            ),
            TimeSlot(
                task_id=following.id, plan_start=datetime(2026, 7, 13, 9, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="blocked",
            ),
        ])
        self.db.add(AuditLog(
            user_name="operator", action="task_delay_reported",
            target_type="time_slot", target_id=completed.time_slots[0].id,
            detail={"task_id": completed.id, "delay_hours": 1, "reason": "实验延期"},
        ))
        self.db.commit()

        self._complete_and_shift(completed.id, datetime(2026, 7, 13, 10, 27))

        self.db.refresh(following)
        shifted = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following.id, TimeSlot.lifecycle_status == "active",
        ).one()
        self.assertEqual(datetime(2026, 7, 13, 10, 30), shifted.plan_start)
        self.assertEqual("delayed", following.delay_status)

    def test_reported_late_completion_is_kept_when_following_task_cannot_shift(self):
        project = Project(
            id=1, name="截止项目", code="DELAY-END",
            end_date=datetime(2026, 7, 13, 17, 30),
        )
        completed = Task(
            project=project, name="方法验证", task_type="test", status="running",
        )
        following = Task(
            project=project, name="报告撰写", task_type="manual", status="scheduled",
        )
        self.db.add_all([project, completed, following])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=completed.id, plan_start=datetime(2026, 7, 13, 8, 30),
                plan_end=datetime(2026, 7, 13, 9, 0), status="running",
            ),
            TimeSlot(
                task_id=following.id, plan_start=datetime(2026, 7, 13, 9, 0),
                plan_end=datetime(2026, 7, 13, 17, 0), status="scheduled",
            ),
        ])
        self.db.add(AuditLog(
            user_name="operator", action="task_delay_reported",
            target_type="time_slot", target_id=completed.time_slots[0].id,
            detail={"task_id": completed.id, "delay_hours": 1, "reason": "实验延期"},
        ))
        self.db.commit()

        result = self._complete_and_shift(completed.id, datetime(2026, 7, 13, 10, 0))

        completed_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == completed.id,
        ).one()
        following_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == following.id,
        ).one()
        self.assertEqual("completed", completed_slot.status)
        self.assertEqual(datetime(2026, 7, 13, 10, 0), completed_slot.actual_end)
        self.assertEqual(datetime(2026, 7, 13, 9, 0), following_slot.plan_start)
        self.assertIn("无法自动顺延", result["message"])

    def _complete_and_shift(self, task_id: int, completed_at: datetime) -> dict:
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
            return complete_task_and_shift(
                self.db,
                task_id,
                actual_end_time=completed_at,
            )


if __name__ == "__main__":
    unittest.main()

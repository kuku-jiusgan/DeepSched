import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import AuditLog, Instrument, InstrumentFault, Project, Task, TaskDependency, TaskExecutionSegment, TimeSlot, User
from app.services.instrument_fault_service import list_open_faults, resolve_fault
from app.services.instrument_fault_schedule_service import (
    _fault_replan_fallback_reasons,
    evaluate_fault_impact,
    shift_faulted_instrument_slots,
)
from app.services.fault_replan_context_service import build_fault_replan_context
from app.services.fault_replan_result_service import build_fault_impact_details
from app.services.scheduler_result_service import supersede_replaceable_slots


def working_options(_db, start: datetime) -> dict:
    return {
        "day_start_minutes": 0,
        "day_end_minutes": 24 * 60,
        "include_weekends": True,
        "include_holidays": True,
        "horizon_end": start + timedelta(days=30),
        "calendar_days": {},
    }


class InstrumentFaultScheduleServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_fault_replan_context_uses_active_future_slots(self):
        task = Task(project_id=1, name="上下文任务", task_type="test", status="scheduled")
        self.db.add(task)
        self.db.flush()
        reported_at = datetime(2026, 8, 10, 9, 0)
        self.db.add_all([
            TimeSlot(task_id=task.id, plan_start=reported_at, plan_end=datetime(2026, 8, 10, 10, 0), status="scheduled"),
            TimeSlot(task_id=task.id, plan_start=reported_at, plan_end=datetime(2026, 8, 10, 11, 0), status="scheduled", lifecycle_status="superseded"),
        ])
        self.db.flush()
        context = build_fault_replan_context(self.db, {task.id}, reported_at, datetime(2026, 8, 12, 9, 0))
        self.assertEqual({task.id}, context["task_ids"])
        self.assertEqual(60, context["remaining_duration_minutes"][task.id])

    def test_fault_impact_details_use_active_replanned_slots(self):
        project = Project(name="风险项目", code="FAULT-DETAIL", end_date=datetime(2026, 8, 11, 9, 0))
        task = Task(project=project, name="重排任务", task_type="test", status="scheduled")
        self.db.add_all([project, task])
        self.db.flush()
        self.db.add_all([
            TimeSlot(task_id=task.id, plan_start=datetime(2026, 8, 10, 8, 30), plan_end=datetime(2026, 8, 10, 9, 30), status="scheduled", lifecycle_status="superseded"),
            TimeSlot(task_id=task.id, plan_start=datetime(2026, 8, 11, 8, 30), plan_end=datetime(2026, 8, 11, 10, 0), status="scheduled"),
        ])
        self.db.flush()
        details = build_fault_impact_details(
            self.db, {task.id}, {task.id: (datetime(2026, 8, 10, 8, 30), datetime(2026, 8, 10, 9, 30))},
        )
        self.assertEqual("2026-08-11T08:30:00", details[0]["shifted_start"])
        self.assertFalse(details[0]["can_shift"])

    def test_replan_supersedes_unstarted_slot_crossing_boundary(self):
        task = Task(project_id=1, name="跨边界计划任务", task_type="test", status="scheduled")
        self.db.add(task)
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id,
            plan_start=datetime(2026, 8, 10, 8, 30),
            plan_end=datetime(2026, 8, 10, 10, 30),
            status="scheduled",
        )
        self.db.add(slot)
        self.db.flush()

        supersede_replaceable_slots(
            self.db, {task.id}, "CP-SAT局部重排", datetime(2026, 8, 10, 9, 0),
        )

        self.assertEqual("superseded", slot.lifecycle_status)

    def test_fault_shifts_pending_slots_on_faulted_instrument(self):
        project = Project(
            name="故障顺延项目",
            code="FAULT-PENDING",
            end_date=datetime(2026, 8, 31, 23, 59, 59),
        )
        instrument = Instrument(code="ZBYY-002-0001", name="三重四极液质联用仪")
        task = Task(
            project=project,
            name="方法开发",
            task_type="test",
            status="paused",
            requires_instrument=True,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=task.id,
                instrument_id=instrument.id,
                plan_start=datetime(2026, 8, 10, 8, 30),
                plan_end=datetime(2026, 8, 10, 20, 0),
                status="pending",
                tier="confirmed",
            ),
            TimeSlot(
                task_id=task.id,
                instrument_id=instrument.id,
                plan_start=datetime(2026, 8, 11, 8, 30),
                plan_end=datetime(2026, 8, 11, 20, 0),
                status="pending",
                tier="confirmed",
            ),
        ])
        self.db.commit()

        impact = self._shift(instrument, datetime(2026, 8, 10, 9, 32), datetime(2026, 8, 12, 9, 32))

        shifted_slots = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == task.id,
            TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.plan_start).all()
        self.assertEqual(1, len(shifted_slots))
        self.assertEqual(datetime(2026, 8, 12, 10, 0), shifted_slots[0].plan_start)
        self.assertEqual("pending", shifted_slots[0].status)
        self.assertEqual(2, impact["shifted_slots"])
        self.assertEqual(1, impact["affected_tasks"])
        self.assertEqual(0, impact["risk_tasks"])

    def test_fault_uses_cp_sat_for_rebuildable_scheduled_slots(self):
        assignee = User(username="solver", display_name="求解员", role="技术员", is_active=True)
        reported_at = datetime(2026, 8, 10, 8, 45)
        estimated_resolved_at = datetime(2026, 8, 11, 8, 45)
        project = Project(
            name="求解器故障项目", code="FAULT-SOLVER", estimated_hours=2,
            start_date=datetime(2026, 8, 10, 8, 30),
            end_date=datetime(2026, 8, 20, 18, 0),
        )
        instrument = Instrument(
            code="ZBYY-002-0009", name="故障仪器",
            availability_status="available", status="fault",
        )
        task = Task(
            project=project, name="待重排任务", task_type="test", status="scheduled",
            requires_instrument=True, requires_human=True, assignee=assignee,
        )
        self.db.add_all([assignee, project, instrument, task])
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 10, 8, 30),
            plan_end=datetime(2026, 8, 10, 10, 30), status="scheduled",
        ))
        self.db.add(InstrumentFault(
            instrument_id=instrument.id, reported_at=reported_at,
            estimated_resolved_at=estimated_resolved_at, status="open",
        ))
        self.db.commit()

        with patch(
            "app.services.scheduler.time_horizon",
            return_value=(reported_at, reported_at + timedelta(days=30), 30 * 24 * 2),
        ), patch(
            "app.services.instrument_fault_notification_service.push_by_rule", return_value=0,
        ), patch(
            "app.services.instrument_fault_schedule_service.notify_rescheduled_tasks_delayed",
        ):
            impact = shift_faulted_instrument_slots(
                self.db, instrument, reported_at, estimated_resolved_at,
            )

        self.assertEqual(1, impact["shifted_slots"])
        old_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == task.id,
            TimeSlot.lifecycle_status == "superseded",
        ).one()
        active_slot = self._only_slot(task.id)
        self.assertLess(old_slot.plan_start, reported_at)
        self.assertGreaterEqual(active_slot.plan_start, estimated_resolved_at)

    def test_fault_includes_unslotted_pending_successor_in_cp_sat_closure(self):
        assignee = User(username="closure", display_name="闭包技术员", role="技术员", is_active=True)
        project = Project(name="故障闭包项目", code="FAULT-CLOSURE")
        instrument = Instrument(code="ZBYY-002-0010", name="故障闭包仪器")
        root_task = Task(
            project=project, name="仪器任务", task_type="test", status="scheduled",
            requires_instrument=True, requires_human=True, assignee=assignee,
        )
        successor = Task(
            project=project, name="未排后继任务", task_type="test", status="pending",
            requires_human=True, assignee=assignee,
        )
        self.db.add_all([assignee, project, instrument, root_task, successor])
        self.db.flush()
        self.db.add_all([
            TaskDependency(task_id=successor.id, predecessor_id=root_task.id),
            TimeSlot(
                task_id=root_task.id, instrument_id=instrument.id,
                plan_start=datetime(2026, 8, 10, 8, 30),
                plan_end=datetime(2026, 8, 10, 10, 30), status="scheduled",
            ),
        ])
        self.db.commit()

        with patch(
            "app.services.instrument_fault_schedule_service.replan_resource_closure",
            return_value={"status": "ok", "schedule_run_id": "closure-test"},
        ) as replan, patch(
            "app.services.instrument_fault_schedule_service.build_fault_impact_details",
            return_value=[],
        ), patch(
            "app.services.instrument_fault_notification_service.push_by_rule", return_value=0,
        ), patch(
            "app.services.instrument_fault_schedule_service.notify_rescheduled_tasks_delayed",
        ):
            shift_faulted_instrument_slots(
                self.db, instrument, datetime(2026, 8, 10, 8, 45), datetime(2026, 8, 11, 8, 45),
            )

        self.assertEqual({root_task.id, successor.id}, replan.call_args.kwargs["seed_task_ids"])

    def test_fault_keeps_blocked_task_out_of_cp_sat_replan(self):
        project = Project(name="阻塞故障项目", code="FAULT-BLOCKED")
        instrument = Instrument(code="ZBYY-002-0012", name="阻塞故障仪器")
        task = Task(
            project=project, name="阻塞任务", task_type="test", status="blocked",
            requires_instrument=True, requires_human=False,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 10, 8, 30),
            plan_end=datetime(2026, 8, 10, 10, 30), status="scheduled",
        ))
        self.db.commit()

        with patch(
            "app.services.instrument_fault_schedule_service.replan_resource_closure",
        ) as replan, patch(
            "app.services.instrument_fault_schedule_service._load_working_options",
            side_effect=working_options,
        ), patch(
            "app.services.instrument_fault_notification_service.push_by_rule", return_value=0,
        ), patch(
            "app.services.instrument_fault_schedule_service.notify_rescheduled_tasks_delayed",
        ):
            shift_faulted_instrument_slots(
                self.db, instrument, datetime(2026, 8, 10, 8, 45), datetime(2026, 8, 11, 8, 45),
            )

        self.assertFalse(replan.called)
        self.db.refresh(task)
        self.assertEqual("blocked", task.status)

    def test_fault_fallback_reasons_identify_execution_and_metadata(self):
        project = Project(name="故障回退诊断项目", code="FAULT-DIAGNOSTIC")
        instrument = Instrument(code="ZBYY-002-0013", name="回退诊断仪器")
        task = Task(
            project=project, name="回退诊断任务", task_type="test",
            status="paused", requires_instrument=True, requires_human=True,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id, instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 10, 8, 30),
            plan_end=datetime(2026, 8, 10, 10, 30), status="paused",
            actual_start=datetime(2026, 8, 10, 8, 30),
        )
        self.db.add(slot)
        self.db.flush()

        reasons = _fault_replan_fallback_reasons(self.db, {task.id}, [slot])

        self.assertEqual(
            [
                "actual_execution_slot",
                "missing_assignee",
                "non_rebuildable_task_status",
                "non_scheduled_slot",
            ],
            reasons,
        )

    def test_fault_preview_uses_same_resource_closure_as_replan(self):
        assignee = User(username="preview", display_name="预览技术员", role="技术员", is_active=True)
        project = Project(name="故障预览项目", code="FAULT-PREVIEW")
        instrument = Instrument(code="ZBYY-002-0011", name="故障预览仪器")
        root_task = Task(
            project=project, name="故障根任务", task_type="test", status="scheduled",
            requires_instrument=True, requires_human=True, assignee=assignee,
        )
        same_owner_task = Task(
            project=project, name="同负责人任务", task_type="test", status="scheduled",
            requires_human=True, assignee=assignee,
        )
        self.db.add_all([assignee, project, instrument, root_task, same_owner_task])
        self.db.flush()
        self.db.add_all([
            TimeSlot(
                task_id=root_task.id, instrument_id=instrument.id,
                plan_start=datetime(2026, 8, 10, 8, 30),
                plan_end=datetime(2026, 8, 10, 10, 30), status="scheduled",
            ),
            TimeSlot(
                task_id=same_owner_task.id,
                plan_start=datetime(2026, 8, 10, 10, 30),
                plan_end=datetime(2026, 8, 10, 11, 30), status="scheduled",
            ),
        ])
        self.db.commit()

        impact = evaluate_fault_impact(
            self.db, instrument, datetime(2026, 8, 10, 8, 45), datetime(2026, 8, 11, 8, 45),
        )

        self.assertEqual({root_task.id, same_owner_task.id}, {
            detail["task_id"] for detail in impact["affected_task_details"]
        })

    def test_fault_cascades_to_dependencies_but_not_manual_same_owner_task(self):
        assignee = User(
            username="analyst",
            display_name="分析员",
            role="技术员",
            is_active=True,
        )
        project = Project(
            name="级联顺延项目",
            code="FAULT-CASCADE",
            end_date=datetime(2026, 8, 31, 23, 59, 59),
        )
        faulted_instrument = Instrument(code="ZBYY-002-0001", name="故障仪器")
        other_instrument = Instrument(code="ZBYY-002-0002", name="关联仪器")
        root_task = Task(
            project=project,
            name="故障仪器任务",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
            requires_human=True,
            assignee=assignee,
        )
        dependency_task = Task(
            project=project,
            name="后继依赖任务",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
        )
        same_owner_task = Task(
            project=project,
            name="同负责人方案撰写",
            task_type="test",
            status="scheduled",
            requires_instrument=False,
            requires_human=True,
            assignee=assignee,
        )
        self.db.add_all([
            assignee,
            project,
            faulted_instrument,
            other_instrument,
            root_task,
            dependency_task,
            same_owner_task,
        ])
        self.db.flush()
        self.db.add(TaskDependency(task_id=dependency_task.id, predecessor_id=root_task.id))
        self.db.add_all([
            TimeSlot(
                task_id=root_task.id,
                instrument_id=faulted_instrument.id,
                plan_start=datetime(2026, 8, 10, 8, 30),
                plan_end=datetime(2026, 8, 10, 9, 30),
                status="scheduled",
            ),
            TimeSlot(
                task_id=same_owner_task.id,
                instrument_id=None,
                plan_start=datetime(2026, 8, 12, 9, 0),
                plan_end=datetime(2026, 8, 12, 10, 0),
                status="scheduled",
            ),
            TimeSlot(
                task_id=dependency_task.id,
                instrument_id=other_instrument.id,
                plan_start=datetime(2026, 8, 10, 12, 0),
                plan_end=datetime(2026, 8, 10, 13, 0),
                status="scheduled",
            ),
        ])
        self.db.commit()

        impact = self._shift(faulted_instrument, datetime(2026, 8, 10, 8, 45), datetime(2026, 8, 12, 8, 45))

        root_slot = self._only_slot(root_task.id)
        same_owner_slot = self._only_slot(same_owner_task.id)
        dependency_slot = self._only_slot(dependency_task.id)
        self.assertGreaterEqual(root_slot.plan_start, datetime(2026, 8, 12, 8, 45))
        self.assertGreaterEqual(same_owner_slot.plan_start, root_slot.plan_end)
        self.assertGreaterEqual(dependency_slot.plan_start, root_slot.plan_end)
        self.assertEqual(3, impact["affected_tasks"])

    def test_fault_allows_shift_past_project_end_and_marks_risk(self):
        project = Project(
            name="截止项目",
            code="FAULT-RISK",
            end_date=datetime(2026, 8, 12, 9, 0),
        )
        instrument = Instrument(code="ZBYY-002-0001", name="故障仪器")
        task = Task(
            project=project,
            name="超期任务",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id,
            instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 10, 8, 30),
            plan_end=datetime(2026, 8, 10, 10, 30),
            status="scheduled",
        ))
        self.db.commit()

        impact = self._shift(instrument, datetime(2026, 8, 10, 8, 45), datetime(2026, 8, 12, 8, 45))

        shifted = self._only_slot(task.id)
        self.assertGreater(shifted.plan_end, project.end_date)
        self.assertEqual(1, impact["risk_tasks"])
        self.assertFalse(impact["affected_task_details"][0]["can_shift"])
        self.assertIn("超期风险", impact["affected_task_details"][0]["reason"])

    def test_fault_keeps_running_status_with_open_execution_segment(self):
        project = Project(name="运行中故障项目", code="FAULT-RUNNING")
        instrument = Instrument(code="ZBYY-002-0001", name="故障仪器")
        task = Task(
            project=project,
            name="方法开发",
            task_type="test",
            status="running",
            requires_instrument=True,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        executed_slot = TimeSlot(
            task_id=task.id,
            instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 12, 8, 30),
            plan_end=datetime(2026, 8, 12, 12, 0),
            status="running",
        )
        future_slot = TimeSlot(
            task_id=task.id,
            instrument_id=instrument.id,
            plan_start=datetime(2026, 8, 13, 8, 30),
            plan_end=datetime(2026, 8, 13, 10, 30),
            status="running",
        )
        self.db.add_all([executed_slot, future_slot])
        self.db.flush()
        self.db.add(TaskExecutionSegment(
            task_id=task.id,
            slot_id=executed_slot.id,
            instrument_id=instrument.id,
            started_at=datetime(2026, 8, 12, 8, 30),
        ))
        self.db.commit()

        self._shift(instrument, datetime(2026, 8, 13, 8, 0), datetime(2026, 8, 14, 8, 0))

        self.db.refresh(task)
        self.assertEqual("running", task.status)

    def test_resolve_fault_shifts_slots_when_actual_resolution_is_late(self):
        project = Project(name="延期维修项目", code="FAULT-LATE")
        instrument = Instrument(code="ZBYY-002-0001", name="故障仪器", status="fault")
        task = Task(
            project=project,
            name="方法开发",
            task_type="test",
            status="scheduled",
            requires_instrument=True,
        )
        self.db.add_all([project, instrument, task])
        self.db.flush()
        fault = InstrumentFault(
            instrument_id=instrument.id,
            reported_at=datetime(2026, 8, 10, 9, 30),
            estimated_resolved_at=datetime(2026, 8, 12, 9, 30),
            status="open",
        )
        self.db.add_all([
            fault,
            TimeSlot(
                task_id=task.id,
                instrument_id=instrument.id,
                plan_start=datetime(2026, 8, 12, 10, 0),
                plan_end=datetime(2026, 8, 12, 12, 0),
                status="scheduled",
            ),
        ])
        self.db.commit()
        actual_resolved_at = datetime(2026, 8, 12, 15, 30)

        with patch(
            "app.services.instrument_fault_service.datetime",
        ) as mocked_datetime, patch(
            "app.services.instrument_fault_schedule_service._load_working_options",
            side_effect=working_options,
        ), patch(
            "app.services.instrument_fault_notification_service.push_by_rule",
            return_value=0,
        ), patch(
            "app.services.instrument_fault_schedule_service.notify_rescheduled_tasks_delayed",
        ):
            mocked_datetime.now.return_value = actual_resolved_at
            resolved = resolve_fault(self.db, instrument.id, fault.id)

        shifted = self._only_slot(task.id)
        self.assertEqual(actual_resolved_at, resolved.resolved_at)
        self.assertGreaterEqual(shifted.plan_start, actual_resolved_at)
        self.assertEqual("resolved", resolved.status)

    def test_fault_list_uses_stored_reschedule_impact(self):
        instrument = Instrument(code="ZBYY-002-0001", name="故障仪器", status="fault")
        self.db.add(instrument)
        self.db.flush()
        fault = InstrumentFault(
            instrument_id=instrument.id,
            reported_at=datetime(2026, 8, 13, 8, 0),
            estimated_resolved_at=datetime(2026, 8, 15, 8, 0),
            status="open",
        )
        self.db.add(fault)
        self.db.flush()
        stored_detail = {
            "task_id": 48,
            "original_start": "2026-08-13T08:30:00",
            "shifted_start": "2026-08-17T08:30:00",
            "can_shift": True,
        }
        self.db.add(AuditLog(
            user_name="system",
            action="instrument_fault_rescheduled",
            target_type="instrument_fault",
            target_id=fault.id,
            detail={
                "impact": {
                    "affected_tasks": 1,
                    "affected_task_details": [stored_detail],
                },
            },
        ))
        self.db.commit()

        listed_fault = list_open_faults(self.db)[0]

        self.assertEqual([stored_detail], listed_fault.affected_tasks)
        self.assertEqual(1, listed_fault.schedule_impact["affected_tasks"])

    def _shift(self, instrument: Instrument, reported_at: datetime, estimated_resolved_at: datetime) -> dict:
        with patch(
            "app.services.instrument_fault_schedule_service._load_working_options",
            side_effect=working_options,
        ), patch(
            "app.services.instrument_fault_notification_service.push_by_rule",
            return_value=0,
        ), patch(
            "app.services.instrument_fault_schedule_service.notify_rescheduled_tasks_delayed",
        ):
            return shift_faulted_instrument_slots(
                self.db,
                instrument,
                reported_at,
                estimated_resolved_at,
            )

    def _only_slot(self, task_id: int) -> TimeSlot:
        return self.db.query(TimeSlot).filter(
            TimeSlot.task_id == task_id,
            TimeSlot.lifecycle_status == "active",
        ).one()


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.scheduler_diagnostics import (
    _project_instrument_intervals,
    schedule_infeasibility_diagnostic,
    schedule_infeasibility_message,
)


class SchedulerInfeasibilityDiagnosticsTest(unittest.TestCase):
    def test_groups_capacity_by_top_level_task_and_instrument(self):
        horizon_start = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        deadline = horizon_start.replace(hour=23, minute=59)
        current_project = SimpleNamespace(
            id=1, code="XM-001", name="当前项目",
            start_date=horizon_start, end_date=deadline, tasks=[],
        )
        other_project = SimpleNamespace(
            id=2, code="XM-002", name="占用项目",
            start_date=horizon_start, end_date=deadline, tasks=[],
        )
        first_root = SimpleNamespace(id=10, name="方法开发", parent=None, instrument_ids=[])
        second_root = SimpleNamespace(id=20, name="方法验证", parent=None, instrument_ids=[])
        first_task = SimpleNamespace(
            id=11, project_id=1, project=current_project, parent=first_root,
            name="开发检测", requires_instrument=True, est_duration_hours=8,
            switchover_hours=0, time_slots=[], status="pending",
        )
        second_task = SimpleNamespace(
            id=21, project_id=1, project=current_project, parent=second_root,
            name="验证检测", requires_instrument=True, est_duration_hours=8,
            switchover_hours=0, time_slots=[], status="pending",
        )
        occupied_slot = SimpleNamespace(
            instrument_id=101, plan_start=horizon_start.replace(hour=9),
            plan_end=horizon_start.replace(hour=11), status="scheduled",
            lifecycle_status="active",
        )
        other_task = SimpleNamespace(
            id=31, project_id=2, project=other_project, parent=None,
            name="其他检测", requires_instrument=True, est_duration_hours=2,
            switchover_hours=0, time_slots=[occupied_slot], status="pending",
            assignee_id=None, latest_due=None, execution_segments=[],
        )
        current_project.tasks = [first_task, second_task]
        other_project.tasks = [other_task]
        first_instrument = SimpleNamespace(id=101, name="GCMS", code="GCMS-01")
        second_instrument = SimpleNamespace(id=202, name="LCMS", code="LCMS-01")
        prefix = list(range(201))

        result = schedule_infeasibility_diagnostic(
            [first_task, second_task, other_task], [], {},
            {11: [first_instrument], 21: [second_instrument], 31: [first_instrument]},
            prefix, {101: prefix, 202: prefix}, horizon_start, 200,
            current_project_id=1,
        )

        groups = result["schedule_failure"]["groups"]
        self.assertEqual([(item["top_level_task_name"], item["instrument_id"]) for item in groups], [
            ("方法开发", 101), ("方法验证", 202),
        ])
        self.assertEqual(groups[0]["occupied_hours"], 2)
        self.assertEqual(groups[0]["details"][0]["project_label"], "XM-002 · 占用项目")
        self.assertEqual(groups[1]["occupied_hours"], 0)
        self.assertNotIn("现有占用明细", result["message"])
        self.assertEqual("scheduling_constraints", result["schedule_failure"]["kind"])
        self.assertNotIn("仪器剩余总工时满足", result["message"])

    def test_counts_waiting_approval_instrument_task_in_current_project(self):
        horizon_start = datetime(2026, 8, 21, 0, 0)
        project = SimpleNamespace(
            id=1, code="XM-001", name="签批项目", start_date=horizon_start,
            end_date=datetime(2026, 9, 1, 0, 0), tasks=[],
        )
        root = SimpleNamespace(id=10, name="标准计划1", parent=None, instrument_ids=[])
        development = SimpleNamespace(
            id=11, project_id=1, project=project, parent=root,
            name="方法开发", requires_instrument=True, est_duration_hours=70,
            switchover_hours=0, time_slots=[], status="pending",
        )
        validation = SimpleNamespace(
            id=12, project_id=1, project=project, parent=root,
            name="方法验证", requires_instrument=True, est_duration_hours=20,
            switchover_hours=0, time_slots=[], status="waiting_external",
        )
        project.tasks = [development, validation]
        instrument = SimpleNamespace(id=101, name="测试仪器", code="CSYQ")
        prefix = list(range(1001))

        result = schedule_infeasibility_diagnostic(
            [development, validation], [], {},
            {11: [instrument], 12: [instrument]}, prefix, {101: prefix},
            horizon_start, 1000, current_project_id=1,
        )

        group = result["schedule_failure"]["groups"][0]
        self.assertEqual(90, group["required_hours"])

    def test_counts_manual_task_bridged_by_same_assignee_and_instrument(self):
        horizon_start = datetime(2026, 8, 21, 0, 0)
        project = SimpleNamespace(
            id=1, code="XM-001", name="夹持项目", start_date=horizon_start,
            end_date=datetime(2026, 9, 1, 0, 0), tasks=[],
        )
        root = SimpleNamespace(id=10, name="标准计划1", parent=None, instrument_ids=[])
        development = SimpleNamespace(
            id=11, project_id=1, project=project, parent=root, name="方法开发",
            requires_instrument=True, requires_human=True, assignee_id=7,
            est_duration_hours=70, switchover_hours=0, time_slots=[], status="pending",
        )
        writing = SimpleNamespace(
            id=12, project_id=1, project=project, parent=root, name="方案撰写",
            requires_instrument=False, requires_human=True, assignee_id=7,
            est_duration_hours=5, switchover_hours=0, time_slots=[], status="pending",
        )
        validation = SimpleNamespace(
            id=13, project_id=1, project=project, parent=root, name="方法验证",
            requires_instrument=True, requires_human=True, assignee_id=7,
            est_duration_hours=20, switchover_hours=0, time_slots=[], status="pending",
        )
        project.tasks = [development, writing, validation]
        instrument = SimpleNamespace(id=101, name="测试仪器", code="CSYQ")
        prefix = list(range(1001))

        result = schedule_infeasibility_diagnostic(
            project.tasks, [(12, 11), (13, 12)], {},
            {11: [instrument], 12: [], 13: [instrument]}, prefix, {101: prefix},
            horizon_start, 1000, current_project_id=1,
        )

        self.assertEqual(
            95,
            result["schedule_failure"]["groups"][0]["required_hours"],
        )

    def test_reports_specific_assignee_and_tasks_when_capacity_is_insufficient(self):
        horizon_start = datetime(2026, 7, 22, 0, 0)
        project = SimpleNamespace(
            code="XM-001", name="多任务项目",
            start_date=horizon_start, end_date=datetime(2026, 7, 31, 23, 59),
        )
        tasks = [
            SimpleNamespace(
                id=1, project_id=1, project=project, parent=None, name="方法开发",
                requires_instrument=False, est_duration_hours=50, switchover_hours=0,
                assignee_id=7, assignee_name="刘文静",
            ),
            SimpleNamespace(
                id=2, project_id=1, project=project, parent=None, name="方案撰写",
                requires_instrument=False, est_duration_hours=30, switchover_hours=0,
                assignee_id=7, assignee_name="刘文静",
            ),
        ]
        sixty_hours = list(range(121))

        message = schedule_infeasibility_message(
            tasks, [], {}, {1: [], 2: []}, sixty_hours, {}, horizon_start, 120,
        )

        self.assertIn("项目【XM-001 · 多任务项目】", message)
        self.assertIn("负责人【刘文静】", message)
        self.assertIn("最多可排 60 小时", message)
        self.assertIn("任务合计 80 小时", message)
        self.assertIn("【方法开发 50小时】", message)

    def test_excludes_movable_tasks_from_resource_occupancy_diagnostic(self):
        horizon_start = datetime(2026, 7, 22, 0, 0)
        current_project = SimpleNamespace(
            id=1, code="XM-001", name="当前项目", start_date=horizon_start,
            end_date=datetime(2026, 8, 31, 23, 59), tasks=[],
        )
        later_project = SimpleNamespace(
            id=2, code="XM-002", name="晚截止项目", start_date=horizon_start,
            end_date=datetime(2026, 9, 16, 23, 59), tasks=[],
        )
        root = SimpleNamespace(id=10, name="方法验证", parent=None, instrument_ids=[101])
        current_task = SimpleNamespace(
            id=11, project_id=1, project=current_project, parent=root,
            name="当前任务", requires_instrument=True, est_duration_hours=2,
            switchover_hours=0, time_slots=[], status="pending",
        )
        movable_slot = SimpleNamespace(
            instrument_id=101, plan_start=horizon_start.replace(hour=9),
            plan_end=horizon_start.replace(hour=11), status="scheduled",
        )
        movable_task = SimpleNamespace(
            id=21, project_id=2, project=later_project, parent=None,
            name="可后移任务", requires_instrument=True, est_duration_hours=2,
            switchover_hours=0, time_slots=[movable_slot], status="scheduled",
            assignee_id=None, latest_due=None, execution_segments=[], children=[],
        )
        current_project.tasks = [current_task]
        later_project.tasks = [movable_task]
        instrument = SimpleNamespace(id=101, name="GCMS", code="GCMS-01")
        prefix = list(range(1000))

        result = schedule_infeasibility_diagnostic(
            [current_task, movable_task], [], {},
            {11: [instrument], 21: [instrument]}, prefix, {101: prefix},
            horizon_start, 1000, current_project_id=1,
            excluded_task_ids={21},
        )

        self.assertEqual([], result["schedule_failure"]["groups"][0]["details"])
        self.assertEqual(0, result["schedule_failure"]["groups"][0]["occupied_hours"])

    def test_counts_scheduled_slot_by_actual_date_before_current_deadline(self):
        horizon_start = (datetime.now() + timedelta(days=1)).replace(
            hour=8, minute=0, second=0, microsecond=0,
        )
        current_deadline = horizon_start + timedelta(days=11, hours=15, minutes=59)
        current_project = SimpleNamespace(
            id=1, code="XM-001", name="当前项目", start_date=horizon_start,
            end_date=current_deadline, tasks=[],
        )
        later_project = SimpleNamespace(
            id=2, code="XM-002", name="晚截止项目", start_date=horizon_start,
            end_date=current_deadline + timedelta(days=16), tasks=[],
        )
        root = SimpleNamespace(id=10, name="方法验证", parent=None, instrument_ids=[101])
        current_task = SimpleNamespace(
            id=11, project_id=1, project=current_project, parent=root,
            name="当前任务", requires_instrument=True, est_duration_hours=65,
            switchover_hours=0, time_slots=[], status="pending",
        )
        occupied_slot = SimpleNamespace(
            instrument_id=101, plan_start=horizon_start + timedelta(days=4, minutes=30),
            plan_end=horizon_start + timedelta(days=5, hours=9, minutes=30), status="scheduled",
            lifecycle_status="active",
        )
        occupied_task = SimpleNamespace(
            id=21, project_id=2, project=later_project, parent=None,
            name="已确认任务", requires_instrument=True, est_duration_hours=33,
            switchover_hours=0, time_slots=[occupied_slot], status="scheduled",
            assignee_id=None, latest_due=None, execution_segments=[], children=[],
        )
        current_project.tasks = [current_task]
        later_project.tasks = [occupied_task]
        instrument = SimpleNamespace(id=101, name="GCMS", code="GCMS-01")
        prefix = list(range(2001))

        result = schedule_infeasibility_diagnostic(
            [current_task, occupied_task], [], {},
            {11: [instrument], 21: [instrument]}, prefix, {101: prefix},
            horizon_start, 2000, current_project_id=1,
        )

        group = result["schedule_failure"]["groups"][0]
        self.assertEqual(33, group["occupied_hours"])
        self.assertEqual(33, group["details"][0]["scheduled_hours"])
        self.assertEqual(0, group["details"][0]["forecast_hours"])

    def test_personnel_waiting_is_not_instrument_occupancy(self):
        start = datetime(2026, 7, 22, 9, 0)
        project = SimpleNamespace(end_date=datetime(2026, 8, 31, 23, 59))
        first = SimpleNamespace(
            id=1, project=project, parent=None, instrument_ids=[101],
            assignee_id=7, est_duration_hours=2, switchover_hours=0,
            status="scheduled", requires_instrument=True,
            time_slots=[SimpleNamespace(
                instrument_id=101, plan_start=start, plan_end=start + timedelta(hours=2),
                status="scheduled",
            )],
        )
        second = SimpleNamespace(
            id=2, project=project, parent=None, instrument_ids=[101],
            assignee_id=7, est_duration_hours=2, switchover_hours=0,
            status="scheduled", requires_instrument=True,
            time_slots=[SimpleNamespace(
                instrument_id=101,
                plan_start=start + timedelta(hours=87.5),
                plan_end=start + timedelta(hours=89.5),
                status="scheduled",
            )],
        )

        _intervals, breakdown = _project_instrument_intervals(
            [first, second], 101, {1: [], 2: []}, start, start + timedelta(days=10),
        )

        self.assertAlmostEqual(4.0, breakdown["slot"])
        self.assertAlmostEqual(85.5, breakdown["waiting"])
        resource_hours = sum(
            (end - begin).total_seconds() / 3600
            for begin, end, _kind in breakdown["resource_intervals"]
        )
        self.assertAlmostEqual(4.0, resource_hours, places=6)

    def test_gaps_between_slots_of_same_task_are_not_personnel_waiting(self):
        start = datetime(2026, 8, 20, 8, 30)
        project = SimpleNamespace(end_date=datetime(2026, 8, 31, 23, 59))
        task = SimpleNamespace(
            id=1, project=project, parent=None, instrument_ids=[101],
            assignee_id=7, est_duration_hours=4, switchover_hours=0,
            status="scheduled", requires_instrument=True,
            time_slots=[
                SimpleNamespace(
                    instrument_id=101, plan_start=start,
                    plan_end=start + timedelta(hours=2), status="scheduled",
                ),
                SimpleNamespace(
                    instrument_id=101, plan_start=start + timedelta(days=1),
                    plan_end=start + timedelta(days=1, hours=2), status="scheduled",
                ),
            ],
        )

        _intervals, breakdown = _project_instrument_intervals(
            [task], 101, {1: []}, start, start + timedelta(days=2),
        )

        self.assertEqual(0, breakdown["waiting"])
    def test_reports_project_window_and_earliest_start_time(self):
        horizon_start = datetime(2026, 7, 17, 8, 30)
        project = SimpleNamespace(
            name="时间冲突项目",
            start_date=datetime(2026, 7, 10, 8, 30),
            end_date=datetime(2026, 7, 20, 1, 14),
        )
        task = SimpleNamespace(
            id=2,
            project_id=1,
            project=project,
            name="方案撰写",
            requires_instrument=False,
            est_duration_hours=2,
            switchover_hours=0,
        )
        zero_working_time = [0] * 200

        message = schedule_infeasibility_message(
            [task],
            [(task.id, 1)],
            {1: 25},
            {task.id: []},
            zero_working_time,
            {},
            horizon_start,
            199,
        )

        self.assertIn("项目【时间冲突项目】", message)
        self.assertIn("任务【方案撰写】", message)
        self.assertIn("项目时间：2026-07-10 08:30 至 2026-07-20 01:14", message)
        self.assertIn("最早可开始时间：2026-07-17 21:00", message)
        self.assertIn("任务需要约 2 小时，剩余有效工时约 0 小时", message)

    def test_fallback_lists_each_involved_project_with_time_and_hours(self):
        horizon_start = datetime(2026, 7, 17, 8, 30)
        first_project = SimpleNamespace(
            name="项目甲",
            start_date=datetime(2026, 7, 1, 8, 30),
            end_date=datetime(2026, 8, 1, 20, 0),
        )
        second_project = SimpleNamespace(
            name="项目乙",
            start_date=datetime(2026, 7, 2, 8, 30),
            end_date=datetime(2026, 9, 1, 20, 0),
        )
        tasks = [
            SimpleNamespace(
                id=1, project_id=1, project=first_project, name="任务甲",
                requires_instrument=True, est_duration_hours=22,
                switchover_hours=0,
            ),
            SimpleNamespace(
                id=2, project_id=2, project=second_project, name="任务乙",
                requires_instrument=True, est_duration_hours=56,
                switchover_hours=0,
            ),
        ]
        all_working_time = list(range(201))
        instruments = {
            task.id: [SimpleNamespace(id=task.id)]
            for task in tasks
        }

        message = schedule_infeasibility_message(
            tasks,
            [],
            {},
            instruments,
            all_working_time,
            {1: all_working_time, 2: all_working_time},
            horizon_start,
            200,
        )

        self.assertIn("【项目甲】", message)
        self.assertIn("待排总工时约 22 小时", message)
        self.assertIn("【项目乙】", message)
        self.assertIn("待排总工时约 56 小时", message)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.api.detection_tasks import _response
from app.services.detection_task_service import (
    DetectionTaskInvalidError,
    create_detection_task,
    delete_detection_task,
    list_detection_tasks,
    update_detection_task,
    DetectionTaskNotFoundError,
)


class DetectionTaskServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(username="manager", display_name="负责人", role="项目管理员", is_active=True)
        self.instrument = Instrument(code="LC-01", name="液相色谱仪")
        self.db.add_all([self.user, self.instrument])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.services.detection_task_service.apply_project_plan")
    def test_creates_top_level_task_without_predecessors_and_schedules_it(self, apply_plan):
        apply_plan.return_value.model_dump.return_value = {"status": "ok", "message": "排程完成"}
        start = datetime(2026, 7, 21)
        data = SimpleNamespace(
            code="JC-001", name="含量检测", client_name="客户A", priority=2,
            manager_id=self.user.id, start_date=start, end_date=start + timedelta(days=3),
            task_type="instrument", est_duration_hours=8, switchover_hours=0.5,
            requires_instrument=True, requires_human=True, allow_split=False,
            allow_transfer=False, instrument_ids=[self.instrument.id], assignee_id=self.user.id,
        )

        project, result = create_detection_task(self.db, data)

        self.assertEqual("detection", project.project_kind)
        self.assertEqual(1, len(project.tasks))
        self.assertIsNone(project.tasks[0].parent_id)
        self.assertEqual([], project.tasks[0].predecessor_ids)
        self.assertEqual("ok", result["status"])
        apply_plan.assert_called_once_with(self.db, project.id)

    def test_detection_tasks_are_separate_from_standard_projects(self):
        detection = Project(code="JC-001", name="检测任务", project_kind="detection")
        self.db.add_all([
            Project(code="P-001", name="普通项目", project_kind="project"),
            detection,
        ])
        self.db.flush()
        self.db.add(Task(project_id=detection.id, name="检测任务", task_type="manual", assignee_id=self.user.id))
        self.db.commit()

        result = list_detection_tasks(self.db, self.user)

        self.assertEqual(["JC-001"], [item.code for item in result])

    def test_technician_only_sees_own_detection_tasks(self):
        self.user.role = "技术员"
        other = User(username="other", display_name="其他负责人", role="技术员", is_active=True)
        own_detection = Project(code="JC-OWN", name="本人检测", project_kind="detection")
        other_detection = Project(code="JC-OTHER", name="他人检测", project_kind="detection")
        self.db.add_all([other, own_detection, other_detection])
        self.db.flush()
        self.db.add_all([
            Task(project_id=own_detection.id, name="本人检测", task_type="manual", assignee_id=self.user.id),
            Task(project_id=other_detection.id, name="他人检测", task_type="manual", assignee_id=other.id),
        ])
        self.db.commit()

        result = list_detection_tasks(self.db, self.user)

        self.assertEqual(["JC-OWN"], [item.code for item in result])

    @patch("app.services.detection_task_service.apply_project_plan")
    def test_group_lead_and_director_can_view_and_edit_all_detection_tasks(self, apply_plan):
        apply_plan.return_value.model_dump.return_value = {"status": "ok", "message": "排程完成"}
        assignee = User(username="assignee", display_name="执行人", role="技术员", is_active=True)
        project = Project(
            code="JC-ALL", name="他人检测", project_kind="detection",
            start_date=datetime(2026, 8, 6), end_date=datetime(2026, 8, 7),
        )
        task = Task(
            project=project, name=project.name, task_type="instrument",
            est_duration_hours=2, assignee=assignee,
            instrument_ids=[self.instrument.id], requires_instrument=True,
        )
        self.db.add_all([assignee, project, task])
        self.db.commit()
        data = SimpleNamespace(
            code=project.code, name="已调整检测", client_name=None, priority=3,
            manager_id=assignee.id, start_date=project.start_date, end_date=project.end_date,
            task_type=task.task_type, est_duration_hours=2, switchover_hours=0,
            requires_instrument=True, requires_human=False, allow_split=False,
            allow_transfer=False, instrument_ids=[self.instrument.id], assignee_id=assignee.id,
        )

        for index, role in enumerate(("技术组长", "分析所所长"), start=1):
            manager = User(
                username=f"manager-{index}", display_name=role, role=role,
                roles=[role], is_active=True,
            )
            self.db.add(manager)
            self.db.commit()

            result = list_detection_tasks(self.db, manager)
            updated_project, _ = update_detection_task(self.db, project.id, data, manager)

            self.assertEqual(["JC-ALL"], [item.code for item in result])
            self.assertEqual("已调整检测", updated_project.name)

    def test_project_manager_cannot_update_another_assignees_detection_task(self):
        assignee = User(username="assignee", display_name="执行人", role="技术员", is_active=True)
        project = Project(
            code="JC-MANAGED", name="项目管理员维护的检测", project_kind="detection",
            start_date=datetime(2026, 8, 6), end_date=datetime(2026, 8, 7),
        )
        self.db.add_all([assignee, project])
        self.db.flush()
        task = Task(
            project_id=project.id, name=project.name, task_type="instrument",
            est_duration_hours=2, assignee_id=assignee.id,
            instrument_ids=[self.instrument.id], requires_instrument=True,
        )
        self.db.add(task)
        self.db.commit()
        data = SimpleNamespace(
            code=project.code, name=project.name, client_name=None, priority=3,
            manager_id=assignee.id, start_date=project.start_date, end_date=project.end_date,
            task_type=task.task_type, est_duration_hours=2, switchover_hours=0,
            requires_instrument=True, requires_human=False, allow_split=False,
            allow_transfer=False, instrument_ids=[self.instrument.id], assignee_id=assignee.id,
        )

        with self.assertRaises(DetectionTaskNotFoundError):
            update_detection_task(self.db, project.id, data, self.user)

    @patch("app.services.detection_task_service.apply_project_plan")
    def test_completed_detection_task_cannot_be_deleted(self, apply_plan):
        apply_plan.return_value.model_dump.return_value = {"status": "ok", "message": "排程完成"}
        start = datetime(2026, 7, 21)
        data = SimpleNamespace(
            code="JC-DONE", name="已完成检测", client_name=None, priority=3,
            manager_id=self.user.id, start_date=start, end_date=start + timedelta(days=1),
            task_type="instrument", est_duration_hours=4, switchover_hours=0,
            requires_instrument=True, requires_human=True, allow_split=False,
            allow_transfer=False, instrument_ids=[self.instrument.id], assignee_id=self.user.id,
        )
        project, _ = create_detection_task(self.db, data)
        project.tasks[0].status = "done"
        self.db.commit()

        with self.assertRaises(DetectionTaskInvalidError):
            delete_detection_task(self.db, project.id, self.user)

    def test_system_admin_can_delete_completed_detection_task(self):
        admin = User(
            username="admin", display_name="管理员", role="系统管理员",
            roles=["系统管理员"], is_active=True,
        )
        project = Project(code="JC-ADMIN-DONE", name="管理员删除", project_kind="detection")
        self.db.add_all([admin, project])
        self.db.flush()
        self.db.add(Task(project_id=project.id, name=project.name, task_type="manual", status="completed"))
        self.db.commit()

        delete_detection_task(self.db, project.id, admin)

        self.assertIsNone(self.db.query(Project).filter(Project.id == project.id).first())

    @patch("app.services.detection_task_service.apply_project_plan")
    def test_updates_detection_task_and_reschedules_it(self, apply_plan):
        apply_plan.return_value.model_dump.return_value = {"status": "ok", "message": "排程完成"}
        start = datetime(2026, 7, 21)
        original = SimpleNamespace(
            code="JC-001", name="原检测", client_name=None, priority=3,
            manager_id=self.user.id, start_date=start, end_date=start + timedelta(days=2),
            task_type="instrument", est_duration_hours=4, switchover_hours=0,
            requires_instrument=True, requires_human=True, allow_split=False,
            allow_transfer=False, instrument_ids=[self.instrument.id], assignee_id=self.user.id,
        )
        project, _ = create_detection_task(self.db, original)
        updated = SimpleNamespace(**{
            **original.__dict__, "code": "JC-002", "name": "日常检测",
            "est_duration_hours": 6,
        })
        apply_plan.reset_mock()

        project, result = update_detection_task(self.db, project.id, updated, self.user)

        self.assertEqual("JC-002", project.code)
        self.assertEqual("日常检测", project.tasks[0].name)
        self.assertEqual(6, project.tasks[0].est_duration_hours)
        self.assertEqual("pending", project.tasks[0].status)
        self.assertEqual("ok", result["status"])
        apply_plan.assert_called_once_with(self.db, project.id)

    @patch("app.services.detection_task_service.apply_project_plan")
    def test_updates_frozen_detection_task_name_and_end_date_without_reschedule(self, apply_plan):
        project = Project(
            code="JC-FROZEN", name="原检测", project_kind="detection",
            start_date=datetime(2026, 8, 6), end_date=datetime(2026, 8, 7, 23, 59, 59),
            priority=3, manager_id=self.user.id,
        )
        task = Task(
            project=project, name="原检测", task_type="instrument",
            status="scheduled", est_duration_hours=4, switchover_hours=0,
            requires_instrument=True, requires_human=True, allow_split=False,
            allow_transfer=False, instrument_ids=[self.instrument.id],
            assignee_id=self.user.id,
        )
        self.db.add_all([project, task])
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id, instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 6, 13, 30),
            plan_end=datetime(2026, 8, 6, 17, 30),
            tier="frozen", status="scheduled",
        ))
        self.db.commit()
        data = SimpleNamespace(
            code=project.code, name="调整后的检测", client_name=project.client_name,
            priority=project.priority, manager_id=project.manager_id,
            start_date=project.start_date, end_date=datetime(2026, 8, 8),
            task_type=task.task_type, est_duration_hours=task.est_duration_hours,
            switchover_hours=task.switchover_hours,
            requires_instrument=task.requires_instrument,
            requires_human=task.requires_human, allow_split=task.allow_split,
            allow_transfer=task.allow_transfer,
            instrument_ids=[self.instrument.id], assignee_id=self.user.id,
        )

        project, result = update_detection_task(self.db, project.id, data, self.user)

        self.assertEqual("调整后的检测", project.name)
        self.assertEqual("调整后的检测", project.tasks[0].name)
        self.assertEqual(datetime(2026, 8, 8, 23, 59, 59), project.end_date)
        self.assertEqual("ok", result["status"])
        apply_plan.assert_not_called()

    def test_rejects_resource_change_for_frozen_detection_task(self):
        other_instrument = Instrument(code="LC-02", name="备用仪器")
        project = Project(
            code="JC-FROZEN-RESOURCE", name="冻结检测", project_kind="detection",
            start_date=datetime(2026, 8, 6), end_date=datetime(2026, 8, 7, 23, 59, 59),
            priority=3, manager_id=self.user.id,
        )
        task = Task(
            project=project, name="冻结检测", task_type="instrument",
            status="scheduled", est_duration_hours=4, switchover_hours=0,
            requires_instrument=True, requires_human=True, allow_split=False,
            allow_transfer=False, instrument_ids=[self.instrument.id],
            assignee_id=self.user.id,
        )
        self.db.add_all([other_instrument, project, task])
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id, instrument_id=self.instrument.id,
            plan_start=datetime(2026, 8, 6, 13, 30),
            plan_end=datetime(2026, 8, 6, 17, 30),
            tier="frozen", status="scheduled",
        ))
        self.db.commit()
        data = SimpleNamespace(
            code=project.code, name=project.name, client_name=project.client_name,
            priority=project.priority, manager_id=project.manager_id,
            start_date=project.start_date, end_date=project.end_date,
            task_type=task.task_type, est_duration_hours=task.est_duration_hours,
            switchover_hours=task.switchover_hours,
            requires_instrument=task.requires_instrument,
            requires_human=task.requires_human, allow_split=task.allow_split,
            allow_transfer=task.allow_transfer,
            instrument_ids=[other_instrument.id], assignee_id=self.user.id,
        )

        with self.assertRaisesRegex(DetectionTaskInvalidError, "不能修改指定仪器"):
            update_detection_task(self.db, project.id, data, self.user)


if __name__ == "__main__":
    unittest.main()


class DetectionTaskResponseTest(unittest.TestCase):
    """接口层要把排程诊断原样带给前台。

    检测任务和计划排程共用同一个求解入口，失败诊断（仪器余量、占用明细、调整
    方案任务号）也是同一份。这里曾经只回传一句 message，前台除了「暂未排入
    日程」什么都显示不出来。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        user = User(username="manager", display_name="负责人", role="项目管理员", is_active=True)
        self.db.add(user)
        self.db.commit()
        project = Project(
            code="JC-900", name="含量检测", project_kind="detection", status="pending",
            priority=2, manager_id=user.id,
            start_date=datetime(2026, 7, 21), end_date=datetime(2026, 7, 24),
        )
        self.db.add(project)
        self.db.flush()
        self.db.add(Task(
            project_id=project.id, name="含量检测", task_type="RCJC_001",
            requires_instrument=True, requires_human=True, est_duration_hours=8,
            switchover_hours=0, allow_split=False, assignee_id=user.id, instrument_ids=[],
        ))
        self.db.commit()
        self.project = project

    def tearDown(self):
        self.db.close()

    def test_response_carries_schedule_failure_diagnostic(self):
        diagnostic = {
            "kind": "scheduling_constraints",
            "summary": "项目未能在截止日期前排入",
            "occupancy": [{"project_label": "测试项目A"}],
            "recommendation_job": {"id": "job-1", "status": "pending"},
        }
        schedule = {"status": "error", "message": "排程失败", "schedule_failure": diagnostic}

        response = _response(self.project, self.db, schedule)

        self.assertEqual("error", response["schedule_status"])
        self.assertEqual(diagnostic, response["schedule_failure"])

    def test_response_without_schedule_leaves_failure_empty(self):
        response = _response(self.project, self.db)

        self.assertIsNone(response["schedule_failure"])

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, User
from app.services.detection_task_service import (
    DetectionTaskInvalidError,
    create_detection_task,
    delete_detection_task,
    list_detection_tasks,
    update_detection_task,
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

    def test_non_admin_only_sees_own_detection_tasks(self):
        other = User(username="other", display_name="其他负责人", role="项目管理员", is_active=True)
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


if __name__ == "__main__":
    unittest.main()

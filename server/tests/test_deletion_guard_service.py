import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.deletion_guard_service import (
    COMPLETED, FREE, STARTED, deletion_block_reason, project_state, task_tree_state,
)
from app.services.detection_task_service import (
    DetectionTaskInvalidError, delete_detection_task,
)
from app.services.project_deletion_service import (
    ProjectDeleteInvalidError, delete_project_plan,
)
from app.services.project_plan_change_service import (
    PlanChangeInvalidError, delete_task_plan,
)


class DeletionGuardTest(unittest.TestCase):
    """已开始的东西非系统管理员不许删，已完成的东西只有系统管理员能删。

    此前三处守卫各写各的，判据、文案、豁免规则都不一样，而且都只看"已完成"、不看
    "已开始"：副本实测中，技术组长把正在跑的检测任务「测试2 · 测试」、进行中的项目
    「XM2026194」和已实际开始的任务「XM2026084 · 气质开发」全都删掉了。

    少数进行中的任务确实删不掉，但报的是"已完成任务不允许删除"——旧判据看的是有没有
    任何一个时间槽是 completed，被切成多段、前几段做完的进行中任务就会命中，操作人
    看到的理由与事实对不上。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.admin = User(
            username="admin", display_name="系统管理员", role="系统管理员",
            roles=["系统管理员"], is_active=True,
        )
        self.tech = User(
            username="tech", display_name="李伟", role="技术组长",
            roles=["技术组长"], is_active=True,
        )
        self.instrument = Instrument(code="LC-01", name="液相色谱仪")
        self.db.add_all([self.admin, self.tech, self.instrument])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _project(self, kind: str, task_status: str, started: bool) -> Project:
        project = Project(code=f"X-{kind}-{task_status}", name="活", project_kind=kind)
        self.db.add(project)
        self.db.flush()
        task = Task(
            project_id=project.id, name="方法开发", task_type="FFKF_001",
            requires_instrument=True, status=task_status, assignee_id=self.tech.id,
        )
        self.db.add(task)
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id, instrument_id=self.instrument.id,
            plan_start=datetime(2026, 9, 7, 8, 30), plan_end=datetime(2026, 9, 7, 12, 30),
            actual_start=datetime(2026, 9, 7, 8, 30) if started else None,
            status="running" if started else "scheduled", tier="confirmed",
        ))
        self.db.commit()
        return project

    def test_state_is_read_from_whether_work_actually_started(self):
        self.assertEqual(FREE, project_state(self._project("project", "scheduled", False)))
        self.assertEqual(STARTED, project_state(self._project("project", "running", True)))
        self.assertEqual(COMPLETED, project_state(self._project("project", "completed", True)))

    def test_a_scheduled_task_whose_slot_already_ran_counts_as_started(self):
        """任务状态还是"已排程"，但时间槽已经真实开始过——那就是开始了。"""
        project = self._project("project", "scheduled", True)

        self.assertEqual(STARTED, task_tree_state(project.tasks[0]))

    def test_reason_says_started_not_completed(self):
        """报错文案要照实说，不能把"已开始"说成"已完成"。"""
        self.assertEqual(
            "已开始的任务不允许删除，请联系系统管理员",
            deletion_block_reason(STARTED, is_system_admin=False, subject="任务"),
        )
        self.assertIsNone(deletion_block_reason(STARTED, is_system_admin=True, subject="任务"))
        self.assertEqual(
            "已完成的任务不允许删除",
            deletion_block_reason(COMPLETED, is_system_admin=False, subject="任务"),
        )
        self.assertIsNone(deletion_block_reason(COMPLETED, is_system_admin=True, subject="任务"))

    def test_completed_project_stays_undeletable_even_for_the_system_admin(self):
        """已结题的项目谁都不能删，这条原有规矩本次不放宽。"""
        self.assertEqual(
            "已完成的项目不允许删除",
            deletion_block_reason(
                COMPLETED, is_system_admin=True, subject="项目",
                admin_may_delete_completed=False,
            ),
        )

    def test_non_admin_cannot_delete_a_started_detection_task(self):
        project = self._project("detection", "running", True)

        with self.assertRaises(DetectionTaskInvalidError) as raised:
            delete_detection_task(self.db, project.id, self.tech)

        self.assertIn("已开始", str(raised.exception))
        delete_detection_task(self.db, project.id, self.admin)
        self.assertIsNone(self.db.query(Project).filter(Project.id == project.id).first())

    def test_non_admin_cannot_delete_a_started_project(self):
        project = self._project("project", "running", True)

        with self.assertRaises(ProjectDeleteInvalidError) as raised:
            delete_project_plan(self.db, project.id, "李伟", is_system_admin=False)

        self.assertIn("已开始", str(raised.exception))
        delete_project_plan(self.db, project.id, "系统管理员", is_system_admin=True)
        self.assertIsNone(self.db.query(Project).filter(Project.id == project.id).first())

    def test_non_admin_cannot_delete_a_started_task(self):
        project = self._project("project", "running", True)
        task_id = project.tasks[0].id

        with self.assertRaises(PlanChangeInvalidError) as raised:
            delete_task_plan(self.db, task_id, is_system_admin=False, actor_name="李伟")

        self.assertIn("已开始", str(raised.exception))
        delete_task_plan(self.db, task_id, is_system_admin=True, actor_name="系统管理员")
        self.assertIsNone(self.db.query(Task).filter(Task.id == task_id).first())

    def test_unstarted_work_is_still_deletable_by_anyone_who_may_edit_it(self):
        """没开工的活照旧谁都能删，这次只收紧"已开始"，不顺手收紧别的。"""
        project = self._project("project", "scheduled", False)
        task_id = project.tasks[0].id

        delete_task_plan(self.db, task_id, is_system_admin=False, actor_name="李伟")

        self.assertIsNone(self.db.query(Task).filter(Task.id == task_id).first())


if __name__ == "__main__":
    unittest.main()

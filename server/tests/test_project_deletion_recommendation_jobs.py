import unittest
import uuid
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (
    InstrumentBridgeReservation, Instrument, Project, ScheduleDeadlineRecommendationJob,
    Task, TaskNightRun, TimeSlot, User,
)
from app.services.detection_task_service import delete_detection_task
from app.services.project_deletion_service import delete_project_plan
from app.services.project_plan_change_service import delete_task_plan


class ProjectDeletionRecommendationJobTest(unittest.TestCase):
    """排程失败过的项目和检测任务，也必须删得掉。

    排程失败时后台会为该项目记一条「调整方案」作业。这张表的外键是 NO ACTION，
    作业行留着项目就删不掉——线上系统管理员删「测试2 · 测试」时报的
    IntegrityError 1451 就是它，一共有 12 个检测任务和 8 个项目卡在这个状态。

    这里的断言不看有没有抛异常：用例跑在 SQLite 上，默认不强制外键，抛不出来。
    要盯的是作业行有没有跟着项目一起走——没有孤儿行，MySQL 那边就不会报错。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.admin = User(
            username="admin", display_name="系统管理员", role="系统管理员",
            roles=["系统管理员"], is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _project_with_failed_schedule(self, kind: str) -> Project:
        project = Project(code=f"JC-{kind}", name="曾经排不下的活", project_kind=kind)
        self.db.add(project)
        self.db.flush()
        self.db.add(Task(
            project_id=project.id, name=project.name, task_type="manual", status="scheduled",
        ))
        # 排程失败留下的调整方案作业，两条：同一个项目反复排程会攒下好几条。
        for _ in range(2):
            self.db.add(ScheduleDeadlineRecommendationJob(
                id=str(uuid.uuid4()),
                project_id=project.id,
                plan_fingerprint="x",
                payload={},
                status="completed",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ))
        self.db.commit()
        return project

    def _remaining_jobs(self, project_id: int) -> int:
        return self.db.query(ScheduleDeadlineRecommendationJob).filter(
            ScheduleDeadlineRecommendationJob.project_id == project_id,
        ).count()

    def test_deleting_detection_task_takes_its_recommendation_jobs_along(self):
        project = self._project_with_failed_schedule("detection")
        project_id = project.id
        self.assertEqual(2, self._remaining_jobs(project_id))

        delete_detection_task(self.db, project_id, self.admin)

        self.assertIsNone(self.db.query(Project).filter(Project.id == project_id).first())
        self.assertEqual(0, self._remaining_jobs(project_id))

    def test_deleting_an_ordinary_project_takes_its_recommendation_jobs_along(self):
        project = self._project_with_failed_schedule("project")
        project_id = project.id

        delete_project_plan(self.db, project_id, "系统管理员")

        self.assertIsNone(self.db.query(Project).filter(Project.id == project_id).first())
        self.assertEqual(0, self._remaining_jobs(project_id))


class TaskTimelineTracesArePurgedTest(unittest.TestCase):
    """删任务和删项目都要把任务在时间轴上留下的痕迹清干净。

    指向 task 和 time_slot 的外键有十来个，删除规则清一色 NO ACTION，漏清一张就
    报 IntegrityError 1451。线上踩过三种：删任务时的 time_slot 与 task_night_run，
    删项目时的 task.parent_id 自引用。这里断言的是"有没有留下孤儿行"，不是"抛没
    抛异常"——用例跑在 SQLite 上，默认不强制外键。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(username="tech", display_name="技术员", role="技术员", is_active=True)
        self.instrument = Instrument(code="LC-01", name="液相色谱仪")
        self.project = Project(code="XM-1", name="有排程的项目", project_kind="project")
        self.db.add_all([self.user, self.instrument, self.project])
        self.db.flush()
        self.parent = Task(
            project_id=self.project.id, name="标准计划", task_type="ROOT", status="pending",
        )
        self.db.add(self.parent)
        self.db.flush()
        self.child = Task(
            project_id=self.project.id, parent_id=self.parent.id, name="方法开发",
            task_type="FFKF_001", requires_instrument=True, status="scheduled",
            assignee_id=self.user.id, est_duration_hours=4,
        )
        self.db.add(self.child)
        self.db.flush()
        self.slot = TimeSlot(
            task_id=self.child.id, instrument_id=self.instrument.id,
            plan_start=datetime(2026, 9, 7, 8, 30), plan_end=datetime(2026, 9, 7, 12, 30),
            status="scheduled", tier="confirmed",
        )
        self.db.add(self.slot)
        self.db.flush()
        self.db.add_all([
            TaskNightRun(
                task_id=self.child.id, slot_id=self.slot.id,
                instrument_id=self.instrument.id, operator_id=self.user.id,
                started_at=datetime(2026, 9, 7, 20, 0),
                ended_at=datetime(2026, 9, 8, 8, 0),
            ),
            InstrumentBridgeReservation(
                schedule_run_id="run-1",
                instrument_id=self.instrument.id, task_id=self.child.id,
                previous_task_id=self.child.id, following_task_id=self.child.id,
                plan_start=datetime(2026, 9, 7, 12, 30),
                plan_end=datetime(2026, 9, 7, 13, 0),
            ),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _orphans(self) -> dict:
        return {
            "任务": self.db.query(Task).count(),
            "时间槽": self.db.query(TimeSlot).count(),
            "夜间运行": self.db.query(TaskNightRun).count(),
            "桥接预留": self.db.query(InstrumentBridgeReservation).count(),
        }

    def test_deleting_a_task_leaves_no_orphan_rows_even_for_a_non_admin(self):
        """allow_completed 说的是权限，不是"要不要真删时间槽"。

        此前普通用户删一个已排程的任务时只把时间槽作废、不删，随后删任务就撞上
        time_slot 外键。线上 8 月有两次就是这么失败的。
        """
        delete_task_plan(self.db, self.child.id, allow_completed=False, actor_name="技术员")

        self.assertEqual({"任务": 1, "时间槽": 0, "夜间运行": 0, "桥接预留": 0}, self._orphans())

    def test_deleting_a_project_removes_parent_and_child_tasks_together(self):
        """父任务和子任务一起删，task.parent_id 自引用外键不能挡路。"""
        delete_project_plan(self.db, self.project.id, "系统管理员")

        self.assertIsNone(self.db.query(Project).filter(Project.id == self.project.id).first())
        self.assertEqual({"任务": 0, "时间槽": 0, "夜间运行": 0, "桥接预留": 0}, self._orphans())


if __name__ == "__main__":
    unittest.main()

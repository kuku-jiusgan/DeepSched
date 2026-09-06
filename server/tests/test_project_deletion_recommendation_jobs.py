import unittest
import uuid
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, ScheduleDeadlineRecommendationJob, Task, User
from app.services.detection_task_service import delete_detection_task


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
        # 普通项目的删除接口最后一步同样是 db.delete(project)，靠的是同一条级联。
        project = self._project_with_failed_schedule("project")
        project_id = project.id

        self.db.delete(project)
        self.db.commit()

        self.assertIsNone(self.db.query(Project).filter(Project.id == project_id).first())
        self.assertEqual(0, self._remaining_jobs(project_id))


if __name__ == "__main__":
    unittest.main()

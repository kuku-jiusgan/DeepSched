import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import AuditLog, Project, Task
from app.schemas.schemas import TaskUpdate
from app.services.project_plan_change_service import update_task_plan
from app.services.project_task_rollup_service import recalculate_project_parent_hours


class ProjectTaskRollupServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.project = Project(name="测试项目", code="ROLLUP-001", priority=1)
        self.db.add(self.project)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def test_parent_hours_equal_leaf_task_sum(self):
        parent = Task(
            project_id=self.project.id,
            name="方法开发",
            task_type="group",
            est_duration_hours=60,
        )
        self.db.add(parent)
        self.db.flush()
        self.db.add_all([
            Task(project_id=self.project.id, parent_id=parent.id, name="LCMS", task_type="test", est_duration_hours=10),
            Task(project_id=self.project.id, parent_id=parent.id, name="GCMS", task_type="test", est_duration_hours=12.5),
        ])
        self.db.flush()

        updated = recalculate_project_parent_hours(self.db, self.project.id)

        self.assertEqual(1, updated)
        self.assertEqual(22.5, parent.est_duration_hours)

    def test_updating_child_recalculates_parent_hours(self):
        parent = Task(
            project_id=self.project.id,
            name="方法验证",
            task_type="group",
            est_duration_hours=8,
        )
        child = Task(
            project_id=self.project.id,
            parent=parent,
            name="LCMS",
            task_type="test",
            est_duration_hours=10,
        )
        self.db.add_all([parent, child])
        self.db.commit()

        update_task_plan(self.db, child.id, TaskUpdate(est_duration_hours=14.5))

        self.db.refresh(parent)
        self.assertEqual(14.5, parent.est_duration_hours)

    def test_task_update_records_readable_business_audit(self):
        task = Task(
            project_id=self.project.id,
            name="方法开发",
            task_type="test",
            est_duration_hours=8,
        )
        self.db.add(task)
        self.db.commit()

        update_task_plan(
            self.db,
            task.id,
            TaskUpdate(est_duration_hours=12),
            actor_name="文霞",
        )

        audit = self.db.query(AuditLog).filter(AuditLog.action == "task_updated").one()
        self.assertEqual("task", audit.detail["category"])
        self.assertEqual("ROLLUP-001 · 方法开发", audit.detail["target_display"])
        self.assertIn("预计工时 8.0 → 12.0", audit.detail["summary"])
        self.assertEqual("预计工时", audit.detail["changes"][0]["field"])


if __name__ == "__main__":
    unittest.main()

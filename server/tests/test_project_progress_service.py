import unittest
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import Project, Task, TimeSlot, User
from app.services.project_progress_service import list_project_progress


class ProjectProgressServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.manager = User(username="manager", display_name="项目负责人", role="项目管理员", roles=["项目管理员"])
        self.db.add(self.manager)
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def test_progress_only_uses_completed_actual_intervals(self):
        project = Project(
            code="XM-001",
            name="项目一",
            manager_id=self.manager.id,
            project_kind="project",
            start_date=datetime(2026, 8, 10, 8),
            end_date=datetime(2026, 8, 20, 18),
        )
        self.db.add(project)
        self.db.flush()
        completed = Task(project_id=project.id, name="已完成", task_type="analysis", status="completed")
        running = Task(project_id=project.id, name="进行中", task_type="analysis", status="running")
        self.db.add_all([completed, running])
        self.db.flush()
        self.db.add_all([
            TimeSlot(task_id=completed.id, plan_start=datetime(2026, 8, 10, 8), plan_end=datetime(2026, 8, 11, 18), actual_start=datetime(2026, 8, 10, 9), actual_end=datetime(2026, 8, 12, 10)),
            TimeSlot(task_id=running.id, plan_start=datetime(2026, 8, 12, 8), plan_end=datetime(2026, 8, 15, 18), actual_start=datetime(2026, 8, 13, 9)),
        ])
        self.db.commit()

        result = list_project_progress(
            self.db,
            SimpleNamespace(id=self.manager.id, role="项目管理员", roles=["项目管理员"]),
        )

        self.assertEqual(1, len(result.items))
        item = result.items[0]
        self.assertEqual(datetime(2026, 8, 10, 9), item.actual_start)
        self.assertEqual(datetime(2026, 8, 12, 10), item.actual_end)
        self.assertEqual(datetime(2026, 8, 13, 9), item.actual_started_at)
        self.assertEqual(1, item.completed_tasks)
        self.assertEqual(2, item.total_tasks)


if __name__ == "__main__":
    unittest.main()

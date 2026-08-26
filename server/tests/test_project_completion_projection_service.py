import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TaskDependency
from app.services.project_completion_projection_service import projected_project_completion


class ProjectCompletionProjectionTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_includes_unscheduled_tasks_after_pending_approval(self):
        project = Project(
            code="P-1",
            name="项目",
            start_date=datetime(2026, 8, 25, 8, 0),
            end_date=datetime(2026, 8, 30, 18, 0),
        )
        gate = Task(
            project=project,
            name="方案签批",
            task_type="approval_gate",
            is_external_gate=True,
            gate_status="submitted",
            expected_approval_at=datetime(2026, 8, 26, 9, 0),
            status="waiting_external",
        )
        validation = Task(
            project=project,
            name="方法验证",
            task_type="FFYZ_001",
            est_duration_hours=2,
            status="waiting_external",
        )
        report = Task(
            project=project,
            name="报告撰写",
            task_type="ZXBG_001",
            est_duration_hours=1,
            status="waiting_external",
        )
        self.db.add_all([project, gate, validation, report])
        self.db.flush()
        self.db.add_all([
            TaskDependency(task_id=validation.id, predecessor_id=gate.id),
            TaskDependency(task_id=report.id, predecessor_id=validation.id),
        ])
        self.db.commit()

        completion = projected_project_completion(self.db, project, {
            "day_start_minutes": 0,
            "day_end_minutes": 24 * 60,
            "include_weekends": True,
            "include_holidays": True,
            "horizon_end": datetime(2026, 9, 30),
            "calendar_days": {},
        })

        self.assertEqual(datetime(2026, 8, 26, 12, 0), completion)


if __name__ == "__main__":
    unittest.main()

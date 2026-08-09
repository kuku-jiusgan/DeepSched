import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.errors import DomainValidationError
from app.models import Task, TimeSlot
from app.services.workspace_command_service import complete_workspace_task


class WorkspaceCommandServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_unstarted_task_cannot_be_completed(self):
        task = Task(project_id=1, name="方法开发", task_type="test", status="scheduled")
        self.db.add(task)
        self.db.flush()
        slot = TimeSlot(
            task_id=task.id,
            plan_start=datetime(2026, 8, 6, 13, 30),
            plan_end=datetime(2026, 8, 6, 20, 0),
            status="scheduled",
        )
        self.db.add(slot)
        self.db.commit()

        with self.assertRaisesRegex(DomainValidationError, "任务尚未开始"):
            complete_workspace_task(self.db, slot.id, True)


if __name__ == "__main__":
    unittest.main()

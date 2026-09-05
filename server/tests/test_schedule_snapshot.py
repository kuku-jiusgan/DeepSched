import unittest
from datetime import datetime

from app.services.schedule_snapshot import (
    ProjectSnapshot,
    ScheduleSnapshot,
    TaskSnapshot,
)


class ScheduleSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = ScheduleSnapshot(
            projects={1: ProjectSnapshot(1, datetime(2026, 9, 30), 1)},
            tasks={1: TaskSnapshot(1, 1, "pending", 2.0, None, (3,), ())},
            instruments={}, time_slots=(), maintenance_windows=(), bridge_reservations=(), dependencies=(), calendar_days=(), rule_params={}, rule_enabled={},
            captured_at=datetime(2026, 9, 1),
        )

    def test_fingerprint_is_stable(self):
        self.assertEqual(self.snapshot.fingerprint(), self.snapshot.fingerprint())

    def test_deadline_override_does_not_mutate_snapshot(self):
        override = datetime(2026, 10, 15)
        deadlines = self.snapshot.with_deadline_overrides({1: override})
        self.assertEqual(override, deadlines[1])
        self.assertEqual(datetime(2026, 9, 30), self.snapshot.projects[1].end_date)

    def test_unknown_deadline_project_fails_fast(self):
        with self.assertRaises(ValueError):
            self.snapshot.with_deadline_overrides({99: datetime(2026, 10, 1)})

if __name__ == "__main__":
    unittest.main()


class DeadlineProbeLeavesNoNetChangeTest(unittest.TestCase):
    """探测候选结题日之后，库里不能留下任何改动。

    探测走的是真实的「保存并排程」入口，它读 Project.end_date，所以必须真的改了
    再跑——但全程在 savepoint 里，跑完必须回滚干净。此前那一版是"完全不写库"，
    改成真实口径后写是必然的，能钉住的是"写完要还原"。
    """

    def test_probe_restores_the_original_deadline(self):
        from datetime import timedelta

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.core.database import Base
        from app.models import Instrument, Project, Task
        from app.services.scheduler_deadline_recommendation import _probe_deadlines

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            original = datetime(2026, 9, 30, 23, 59, 59)
            db.add(Instrument(code="PROBE-INST", name="探测仪器",
                              availability_status="available", status="idle"))
            project = Project(code="SIM-1", name="模拟项目", priority=3, end_date=original)
            db.add(project)
            db.flush()
            task = Task(project_id=project.id, name="方法开发", task_type="test",
                        status="pending", est_duration_hours=2,
                        requires_instrument=False, requires_human=False)
            db.add(task)
            db.commit()

            verdict = _probe_deadlines(
                db, None, {project.id: original + timedelta(days=5)},
                {"current_project_id": project.id, "task_ids": [task.id]},
            )

            self.assertIn(verdict, {"feasible", "infeasible"})
            db.rollback()
            db.refresh(project)
            self.assertEqual(original, project.end_date)
        finally:
            db.close()

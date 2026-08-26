import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot, User
from app.services.task_pause_service import pause_and_switch_task
from app.services.task_execution_service import start_task_execution


class TaskPauseFollowupOrderTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        self.operator = User(username="tech", display_name="技术员", role="技术员")
        self.instrument = Instrument(code="LCMS-01", name="液质联用仪")
        self.project_a = Project(code="A", name="项目A")
        self.project_b = Project(code="B", name="项目B")
        self.db.add_all([self.operator, self.instrument, self.project_a, self.project_b])
        self.db.flush()

    def tearDown(self):
        self.db.close()

    def test_source_continuous_successor_precedes_ordinary_queue_after_switch(self):
        source_parent, source, source_followup = self._task_group(self.project_a, "A")
        _, target, _ = self._task_group(self.project_b, "B")
        ordinary = Task(
            project_id=self.project_a.id,
            name="普通队列任务",
            task_type="FFYZ_001",
            requires_instrument=True,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=2,
        )
        self.db.add(ordinary)
        self.db.flush()
        now = datetime.now().replace(second=0, microsecond=0)
        source_slot = self._slot(source, now - timedelta(hours=1), now + timedelta(hours=3))
        target_slot = self._slot(target, now + timedelta(hours=3), now + timedelta(hours=6))
        self._slot(source_followup, now + timedelta(hours=6), now + timedelta(hours=8), False)
        self._slot(ordinary, now + timedelta(hours=8), now + timedelta(hours=10))
        self.db.add(TaskDependency(
            task_id=source_followup.id,
            predecessor_id=source.id,
            dependency_type="continuous_successor",
        ))
        self.db.commit()
        start_task_execution(self.db, source_slot.id, self.operator.id)
        self.db.commit()

        pause_and_switch_task(
            self.db, source_slot.id, "切换项目", self.operator, target_slot.id,
        )
        self.db.commit()

        source_end = self._active_slots(source)[-1].plan_end
        followup_slots = self._active_slots(source_followup)
        ordinary_slots = self._active_slots(ordinary)
        self.assertLessEqual(source_end, followup_slots[0].plan_start)
        self.assertLessEqual(followup_slots[-1].plan_end, ordinary_slots[0].plan_start)
        self.assertEqual(source_parent.id, source_followup.parent_id)

    def _task_group(self, project: Project, suffix: str) -> tuple[Task, Task, Task]:
        parent = Task(project_id=project.id, name=f"标准计划{suffix}", task_type="ROOT")
        self.db.add(parent)
        self.db.flush()
        method = Task(
            project_id=project.id,
            parent_id=parent.id,
            name=f"方法开发{suffix}",
            task_type="FFKF_001",
            requires_instrument=True,
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=3,
        )
        followup = Task(
            project_id=project.id,
            parent_id=parent.id,
            name=f"方案撰写{suffix}",
            task_type="QCFA_001",
            requires_human=True,
            assignee_id=self.operator.id,
            status="scheduled",
            est_duration_hours=2,
        )
        self.db.add_all([method, followup])
        self.db.flush()
        return parent, method, followup

    def _slot(
        self,
        task: Task,
        start: datetime,
        end: datetime,
        uses_instrument: bool = True,
    ) -> TimeSlot:
        slot = TimeSlot(
            task_id=task.id,
            instrument_id=self.instrument.id if uses_instrument else None,
            plan_start=start,
            plan_end=end,
            status="scheduled",
            tier="confirmed",
        )
        self.db.add(slot)
        return slot

    def _active_slots(self, task: Task) -> list[TimeSlot]:
        return self.db.query(TimeSlot).filter(
            TimeSlot.task_id == task.id,
            TimeSlot.lifecycle_status == "active",
        ).order_by(TimeSlot.plan_start, TimeSlot.id).all()


if __name__ == "__main__":
    unittest.main()

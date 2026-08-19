import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TimeSlot
from app.services.project_plan_apply_service import (
    _build_project_impacts,
    _priority_insert_dependencies,
    _project_impact_message,
)
from app.services.schedule_insert_service import (
    _build_impacts,
    _load_lower_priority_movable_tasks,
)


class SamePriorityScheduleInsertTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(Instrument(id=1, code="INST-001", name="测试仪器"))

    def tearDown(self):
        self.db.close()

    def _scheduled_project(self, code: str, priority: int, task_id: int):
        future_start = datetime.now() + timedelta(days=7)
        project = Project(
            code=code,
            name=f"项目{code}",
            priority=priority,
            end_date=datetime(2026, 8, 31, 18, 0),
        )
        self.db.add(project)
        self.db.flush()
        task = Task(
            id=task_id,
            project_id=project.id,
            name=f"任务{code}",
            task_type="test",
            requires_instrument=True,
            instrument_ids=[1],
            status="scheduled",
        )
        self.db.add(task)
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id,
            instrument_id=1,
            plan_start=future_start,
            plan_end=future_start + timedelta(hours=4),
            tier="confirmed",
            status="scheduled",
        ))
        return project, task

    def test_same_priority_unstarted_project_can_move(self):
        _, unstarted_task = self._scheduled_project("B", 3, 1)
        started_project, _ = self._scheduled_project("C", 3, 2)
        self.db.flush()
        started_slot = self.db.query(TimeSlot).join(Task).filter(
            Task.project_id == started_project.id,
        ).one()
        started_slot.actual_start = datetime(2026, 8, 3, 8, 30)
        self.db.commit()

        movable = _load_lower_priority_movable_tasks(
            self.db,
            insert_priority=3,
            excluded_task_ids=set(),
            selected_instrument_ids={1},
            include_same_priority=True,
            unstarted_projects_only=True,
        )

        self.assertEqual([unstarted_task.id], [task.id for task in movable])

    def test_priority_insert_does_not_move_partly_frozen_task(self):
        _, frozen_task = self._scheduled_project("B", 3, 1)
        self.db.flush()
        frozen_slot = self.db.query(TimeSlot).filter(
            TimeSlot.task_id == frozen_task.id,
        ).one()
        frozen_slot.tier = "frozen"
        self.db.commit()

        movable = _load_lower_priority_movable_tasks(
            self.db,
            insert_priority=2,
            excluded_task_ids=set(),
            selected_instrument_ids={1},
        )

        self.assertEqual([], [task.id for task in movable])

    def test_detection_priority_insert_enforces_selected_task_first(self):
        selected_project = Project(
            code="A", name="二级检测", priority=2, project_kind="detection",
        )
        movable_project = Project(code="B", name="三级项目", priority=3)
        self.db.add_all([selected_project, movable_project])
        self.db.flush()
        selected = Task(
            project=selected_project, name="元素杂质检测", task_type="test",
            instrument_ids=[1], assignee_id=10,
        )
        movable = Task(
            project=movable_project, name="方法验证", task_type="test",
            instrument_ids=[1], assignee_id=20,
        )
        self.db.add_all([selected, movable])
        self.db.flush()

        dependencies = _priority_insert_dependencies(
            selected_project, [selected], [movable],
        )

        self.assertEqual([(movable.id, selected.id)], dependencies)

    def test_project_impact_reports_delay_and_deadline_risk(self):
        project, task = self._scheduled_project("B", 3, 1)
        original = datetime(2026, 8, 30, 18, 0)
        delayed = datetime(2026, 9, 1, 18, 0)

        impacts = _build_project_impacts(
            [task],
            [],
            {project.id: original},
            {project.id: delayed},
        )
        message = _project_impact_message(impacts)

        self.assertEqual(48, impacts[0].delay_hours)
        self.assertTrue(impacts[0].exceeds_end_date)
        self.assertEqual(24, impacts[0].overdue_hours)
        self.assertIn("预计顺延 48 小时", message)
        self.assertIn("超过结题日期 24 小时", message)

    def test_project_plan_impacts_include_inserted_and_shifted_roles(self):
        _, inserted_task = self._scheduled_project("A", 2, 1)
        _, shifted_task = self._scheduled_project("B", 3, 2)
        self.db.flush()
        old_windows = {
            shifted_task.id: (
                datetime(2026, 8, 3, 8, 30),
                datetime(2026, 8, 3, 12, 30),
            ),
        }
        new_windows = {
            inserted_task.id: (
                datetime(2026, 8, 3, 8, 30),
                datetime(2026, 8, 3, 12, 30),
            ),
            shifted_task.id: (
                datetime(2026, 8, 3, 13, 30),
                datetime(2026, 8, 3, 17, 30),
            ),
        }

        impacts = _build_impacts(
            [inserted_task, shifted_task],
            {inserted_task.id},
            old_windows,
            new_windows,
            {inserted_task.id: "inserted", shifted_task.id: "shifted"},
        )

        roles = {impact.task_id: impact.impact_role for impact in impacts}
        self.assertEqual("inserted", roles[inserted_task.id])
        self.assertEqual("shifted", roles[shifted_task.id])

    def test_unchanged_task_is_not_reported_as_shifted(self):
        _, unchanged_task = self._scheduled_project("B", 3, 1)
        unchanged_window = (
            datetime(2026, 8, 3, 11, 0),
            datetime(2026, 8, 11, 12, 0),
        )

        impacts = _build_impacts(
            [unchanged_task],
            set(),
            {unchanged_task.id: unchanged_window},
            {unchanged_task.id: unchanged_window},
            {unchanged_task.id: "shifted"},
        )

        self.assertEqual([], impacts)


if __name__ == "__main__":
    unittest.main()

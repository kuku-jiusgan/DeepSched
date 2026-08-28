import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from ortools.sat.python import cp_model
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task
from app.services.scheduler import SchedulerService


class SchedulerVariableBoundsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.horizon_start = (datetime.now() + timedelta(days=1)).replace(
            hour=8, minute=30, second=0, microsecond=0,
        )

    def tearDown(self):
        self.db.close()

    def test_instrument_variables_use_existing_task_window(self):
        project_start = self.horizon_start + timedelta(days=1)
        project_end = self.horizon_start + timedelta(days=3)
        project = Project(
            name="变量范围测试",
            code="VARIABLE-BOUNDS",
            estimated_hours=10,
            start_date=project_start,
            end_date=project_end,
        )
        instrument = Instrument(
            code="VARIABLE-BOUNDS-INST",
            name="变量范围测试仪器",
            availability_status="available",
            status="idle",
        )
        self.db.add_all([project, instrument])
        self.db.flush()
        task = Task(
            project_id=project.id,
            name="变量范围任务",
            task_type="test",
            requires_instrument=True,
            requires_human=False,
            est_duration_hours=1,
            instrument_ids=[instrument.id],
            status="pending",
        )
        self.db.add(task)
        self.db.flush()

        original_solver = cp_model.CpSolver
        captured_models = []

        class CapturingSolver(original_solver):
            def Solve(self, model, solution_callback=None):
                captured_models.append(model.Proto())
                return super().Solve(model, solution_callback)

        total_units = 10 * 48
        with (
            patch(
                "app.services.scheduler.time_horizon",
                return_value=(
                    self.horizon_start,
                    self.horizon_start + timedelta(days=10),
                    total_units,
                ),
            ),
            patch("app.services.scheduler.cp_model.CpSolver", CapturingSolver),
        ):
            result = SchedulerService(self.db).generate(
                project_ids=[project.id],
                current_project_id=project.id,
                commit=False,
            )

        variables = {
            variable.name: list(variable.domain)
            for variable in captured_models[0].variables
        }
        task_start_unit = 48
        task_end_unit = 3 * 48
        duration_units = 2
        self.assertEqual("ok", result["status"])
        self.assertEqual(
            [task_start_unit, task_end_unit - duration_units],
            variables[f"start_t{task.id}"],
        )
        self.assertEqual(
            [task_start_unit + duration_units, task_end_unit],
            variables[f"end_t{task.id}"],
        )
        self.assertEqual(
            [0, 0, task_start_unit, task_end_unit - duration_units],
            variables[f"start_t{task.id}_i{instrument.id}"],
        )
        self.assertEqual(
            [0, 0, task_start_unit + duration_units, task_end_unit],
            variables[f"end_t{task.id}_i{instrument.id}"],
        )


if __name__ == "__main__":
    unittest.main()

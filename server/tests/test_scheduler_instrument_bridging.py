import unittest
from types import SimpleNamespace
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ortools.sat.python import cp_model

from app.services.scheduler_instrument_bridging import (
    add_instrument_bridge_intervals,
    bridged_instrument_hours,
    instrument_bridge_candidates,
)
from app.core.database import Base
from app.models import Project, Task, TimeSlot
from app.services.instrument_bridge_sync_service import rebuild_instrument_bridge_reservations


def _task(task_id, assignee_id, requires_instrument, hours=2):
    return SimpleNamespace(
        id=task_id,
        assignee_id=assignee_id,
        requires_human=True,
        requires_instrument=requires_instrument,
        est_duration_hours=hours,
        switchover_hours=0,
    )


class SchedulerInstrumentBridgingTest(unittest.TestCase):
    def setUp(self):
        self.instrument = SimpleNamespace(id=101)
        self.previous = _task(1, 7, True)
        self.manual = _task(2, 7, False, 5)
        self.following = _task(3, 7, True)
        self.tasks = [self.previous, self.manual, self.following]
        self.dependencies = [(2, 1), (3, 2)]

    def test_manual_task_is_bridged_by_same_assignee_and_instrument(self):
        compatibility = {1: [self.instrument], 2: [], 3: [self.instrument]}

        candidates = instrument_bridge_candidates(
            self.tasks, self.dependencies, compatibility,
        )

        self.assertEqual([(2, 1, 3, 101)], candidates)
        self.assertEqual(
            5,
            bridged_instrument_hours(
                self.tasks, self.dependencies, compatibility, 101,
            ),
        )

    def test_different_assignee_does_not_bridge(self):
        self.following.assignee_id = 8
        compatibility = {1: [self.instrument], 2: [], 3: [self.instrument]}

        self.assertEqual(
            [],
            instrument_bridge_candidates(
                self.tasks, self.dependencies, compatibility,
            ),
        )

    def test_different_instruments_do_not_bridge(self):
        compatibility = {
            1: [self.instrument],
            2: [],
            3: [SimpleNamespace(id=202)],
        }

        self.assertEqual(
            [],
            instrument_bridge_candidates(
                self.tasks, self.dependencies, compatibility,
            ),
        )

    def test_bridge_interval_blocks_another_instrument_task(self):
        model = cp_model.CpModel()
        starts = {task.id: model.NewIntVar(0, 20, f"start_{task.id}") for task in self.tasks}
        ends = {task.id: model.NewIntVar(0, 20, f"end_{task.id}") for task in self.tasks}
        for task, start in zip(self.tasks, (0, 2, 7)):
            model.Add(starts[task.id] == start)
            model.Add(ends[task.id] == start + int(task.est_duration_hours))
        presences = {
            (1, 101): model.NewConstant(1),
            (3, 101): model.NewConstant(1),
        }
        capacity_intervals = {101: []}
        compatibility = {1: [self.instrument], 2: [], 3: [self.instrument]}
        add_instrument_bridge_intervals(
            model, self.tasks, self.dependencies, compatibility,
            starts, ends, capacity_intervals, presences, 20,
        )
        competing = model.NewIntervalVar(3, 1, 4, "competing")
        model.AddNoOverlap([*capacity_intervals[101], competing])

        status = cp_model.CpSolver().Solve(model)

        self.assertEqual(cp_model.INFEASIBLE, status)

    def test_bridge_result_retains_reservation_metadata(self):
        model = cp_model.CpModel()
        starts = {task.id: model.NewIntVar(0, 20, f"start_{task.id}") for task in self.tasks}
        ends = {task.id: model.NewIntVar(0, 20, f"end_{task.id}") for task in self.tasks}
        for task, start in zip(self.tasks, (0, 2, 7)):
            model.Add(starts[task.id] == start)
            model.Add(ends[task.id] == start + int(task.est_duration_hours))
        bridges = add_instrument_bridge_intervals(
            model, self.tasks, self.dependencies, {1: [self.instrument], 2: [], 3: [self.instrument]},
            starts, ends, {(101): []}, {(1, 101): model.NewConstant(1), (3, 101): model.NewConstant(1)}, 20,
        )
        self.assertEqual(1, len(bridges))
        self.assertEqual(self.manual.id, bridges[0]["task_id"])
        self.assertEqual(self.instrument.id, bridges[0]["instrument_id"])

    def test_completed_manual_task_does_not_create_bridge_reservation(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        project = Project(id=1, code="P1", name="项目")
        previous = Task(id=1, project_id=1, name="前序", task_type="test", status="completed", requires_instrument=True, requires_human=True, assignee_id=7)
        manual = Task(id=2, project_id=1, name="方案", task_type="test", status="completed", requires_instrument=False, requires_human=True, assignee_id=7)
        following = Task(id=3, project_id=1, name="后续", task_type="test", status="scheduled", requires_instrument=True, requires_human=True, assignee_id=7)
        db.add_all([project, previous, manual, following])
        db.add_all([
            TimeSlot(task_id=1, instrument_id=101, plan_start=datetime(2026, 8, 28, 8), plan_end=datetime(2026, 8, 28, 10), status="completed", actual_start=datetime(2026, 8, 28, 8), actual_end=datetime(2026, 8, 28, 10)),
            TimeSlot(task_id=2, instrument_id=None, plan_start=datetime(2026, 8, 28, 10), plan_end=datetime(2026, 8, 28, 12), status="completed"),
            TimeSlot(task_id=3, instrument_id=101, plan_start=datetime(2026, 8, 28, 12), plan_end=datetime(2026, 8, 28, 14), status="scheduled"),
        ])
        db.commit()
        self.assertEqual(0, rebuild_instrument_bridge_reservations(db))
        self.assertEqual(0, db.query(TimeSlot).filter(TimeSlot.task_id == 2).count() - 1)
        db.close()


if __name__ == "__main__":
    unittest.main()

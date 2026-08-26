import unittest
from types import SimpleNamespace

from ortools.sat.python import cp_model

from app.services.scheduler_instrument_bridging import (
    add_instrument_bridge_intervals,
    bridged_instrument_hours,
    instrument_bridge_candidates,
)


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


if __name__ == "__main__":
    unittest.main()

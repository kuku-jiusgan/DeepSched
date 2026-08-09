import unittest
from datetime import datetime
from types import SimpleNamespace

from ortools.sat.python import cp_model

from app.services.scheduler_objective import add_scheduler_objective


class SchedulerObjectiveTest(unittest.TestCase):
    def test_sibling_group_completion_moves_later_sibling_forward(self):
        model = cp_model.CpModel()
        project = SimpleNamespace(priority=1, end_date=None)
        tasks = [
            SimpleNamespace(
                id=1, parent_id=10, priority_weight=1, project=project,
                created_at=datetime(2026, 1, 1),
            ),
            SimpleNamespace(
                id=2, parent_id=None, priority_weight=1, project=project,
                created_at=datetime(2026, 1, 2),
            ),
            SimpleNamespace(
                id=3, parent_id=10, priority_weight=1, project=project,
                created_at=datetime(2026, 1, 3),
            ),
        ]
        starts = {
            1: model.NewIntVar(0, 0, "start_1"),
            2: model.NewIntVar(0, 1, "start_2"),
            3: model.NewIntVar(0, 1, "start_3"),
        }
        ends = {
            1: model.NewIntVar(1, 1, "end_1"),
            2: model.NewIntVar(1, 2, "end_2"),
            3: model.NewIntVar(1, 2, "end_3"),
        }
        model.Add(ends[2] == starts[2] + 1)
        model.Add(ends[3] == starts[3] + 1)
        model.AddNoOverlap([
            model.NewIntervalVar(starts[2], 1, ends[2], "task_2"),
            model.NewIntervalVar(starts[3], 1, ends[3], "task_3"),
        ])
        tardiness = {}
        for task in tasks:
            tardiness[task.id] = model.NewIntVar(0, 0, f"tardy_{task.id}")

        add_scheduler_objective(
            model,
            tasks,
            starts,
            ends,
            tardiness,
            [],
            [],
            2,
            100,
            {10: 2},
        )
        solver = cp_model.CpSolver()

        self.assertEqual(cp_model.OPTIMAL, solver.Solve(model))
        self.assertLess(solver.Value(ends[3]), solver.Value(ends[2]))

    def test_remaining_sibling_is_prioritized_when_other_sibling_is_fixed(self):
        model = cp_model.CpModel()
        project = SimpleNamespace(priority=3, end_date=None)
        tasks = [
            SimpleNamespace(
                id=1, parent_id=None, priority_weight=1, project=project,
                created_at=datetime(2026, 1, 1),
            ),
            SimpleNamespace(
                id=2, parent_id=10, priority_weight=1, project=project,
                created_at=datetime(2026, 1, 2),
            ),
        ]
        starts = {
            1: model.NewIntVar(0, 1, "start_1"),
            2: model.NewIntVar(0, 1, "start_2"),
        }
        ends = {
            1: model.NewIntVar(1, 2, "end_1"),
            2: model.NewIntVar(1, 2, "end_2"),
        }
        intervals = []
        for task in tasks:
            model.Add(ends[task.id] == starts[task.id] + 1)
            intervals.append(model.NewIntervalVar(
                starts[task.id],
                1,
                ends[task.id],
                f"task_{task.id}",
            ))
        model.AddNoOverlap(intervals)
        tardiness = {
            task.id: model.NewIntVar(0, 0, f"tardy_{task.id}")
            for task in tasks
        }

        add_scheduler_objective(
            model,
            tasks,
            starts,
            ends,
            tardiness,
            [],
            [],
            2,
            100,
            {10: 2},
        )
        solver = cp_model.CpSolver()

        self.assertEqual(cp_model.OPTIMAL, solver.Solve(model))
        self.assertLess(solver.Value(ends[2]), solver.Value(ends[1]))

    def test_dependency_gap_is_minimized(self):
        model = cp_model.CpModel()
        project = SimpleNamespace(priority=1, end_date=None)
        tasks = [
            SimpleNamespace(id=1, parent_id=None, priority_weight=1, project=project, created_at=datetime(2026, 1, 1)),
            SimpleNamespace(id=2, parent_id=None, priority_weight=1, project=project, created_at=datetime(2026, 1, 2)),
        ]
        starts = {1: model.NewIntVar(0, 0, "start_1"), 2: model.NewIntVar(1, 5, "start_2")}
        ends = {1: model.NewIntVar(1, 1, "end_1"), 2: model.NewIntVar(2, 6, "end_2")}
        model.Add(ends[2] == starts[2] + 1)
        tardiness = {task.id: model.NewIntVar(0, 0, f"tardy_{task.id}") for task in tasks}
        gap = model.NewIntVar(0, 5, "dependency_gap_1_2")
        model.Add(gap >= starts[2] - ends[1])
        add_scheduler_objective(model, tasks, starts, ends, tardiness, [], [], 6, 0, {}, dependency_gap_penalties=[gap])
        solver = cp_model.CpSolver()
        self.assertEqual(cp_model.OPTIMAL, solver.Solve(model))
        self.assertEqual(1, solver.Value(starts[2]))


if __name__ == "__main__":
    unittest.main()

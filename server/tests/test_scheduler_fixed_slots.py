import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from ortools.sat.python import cp_model
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import InstrumentBridgeReservation, Project, Task, TimeSlot
from app.services.scheduler_fixed_slots import (
    add_human_capacity_constraints,
    add_instrument_capacity_constraints,
    load_fixed_bridge_reservations,
    load_fixed_slots,
)
from app.services.scheduler_result_service import supersede_replaceable_slots


class SchedulerFixedSlotsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_unexecuted_completed_segments_are_not_fixed(self):
        executed = TimeSlot(
            task_id=1, instrument_id=1,
            plan_start=datetime(2026, 7, 13, 8, 30),
            plan_end=datetime(2026, 7, 13, 20, 0),
            actual_start=datetime(2026, 7, 13, 8, 30),
            actual_end=datetime(2026, 7, 13, 12, 0),
            status="completed",
        )
        unexecuted = TimeSlot(
            task_id=1, instrument_id=1,
            plan_start=datetime(2026, 7, 14, 8, 30),
            plan_end=datetime(2026, 7, 14, 20, 0),
            status="completed",
        )
        scheduled = TimeSlot(
            task_id=2, instrument_id=1,
            plan_start=datetime(2026, 7, 15, 8, 30),
            plan_end=datetime(2026, 7, 15, 10, 0),
            status="scheduled",
        )
        self.db.add_all([executed, unexecuted, scheduled])
        self.db.commit()

        fixed_slots = load_fixed_slots(self.db)

        self.assertEqual({executed.id, scheduled.id}, {slot.id for slot in fixed_slots})

    def test_superseded_slot_does_not_reserve_fixed_capacity(self):
        active_slot = TimeSlot(
            task_id=1, instrument_id=1,
            plan_start=datetime(2026, 7, 15, 8, 30),
            plan_end=datetime(2026, 7, 15, 10, 0),
            status="scheduled",
        )
        superseded_slot = TimeSlot(
            task_id=2, instrument_id=1,
            plan_start=datetime(2026, 7, 15, 10, 0),
            plan_end=datetime(2026, 7, 15, 12, 0),
            status="scheduled", lifecycle_status="superseded",
        )
        self.db.add_all([active_slot, superseded_slot])
        self.db.commit()

        fixed_slots = load_fixed_slots(self.db)

        self.assertEqual([active_slot.id], [slot.id for slot in fixed_slots])

    def test_manual_task_slot_is_loaded_as_fixed(self):
        manual_slot = TimeSlot(
            task_id=1, instrument_id=None,
            plan_start=datetime(2026, 7, 15, 8, 30),
            plan_end=datetime(2026, 7, 15, 10, 0),
            status="scheduled",
        )
        self.db.add(manual_slot)
        self.db.commit()

        fixed_slots = load_fixed_slots(self.db)

        self.assertEqual([manual_slot.id], [slot.id for slot in fixed_slots])

    def test_bridge_reservation_is_loaded_as_fixed_for_its_instrument(self):
        reservation = InstrumentBridgeReservation(
            task_id=1, instrument_id=2, previous_task_id=3, following_task_id=4,
            schedule_run_id="run-1", plan_start=datetime(2026, 7, 15, 8, 30),
            plan_end=datetime(2026, 7, 15, 10, 0),
        )
        self.db.add(reservation)
        self.db.commit()

        reservations = load_fixed_bridge_reservations(
            self.db, relevant_instrument_ids={2},
        )

        self.assertEqual([reservation.id], [item.id for item in reservations])

    def test_protected_slots_for_replanned_tasks_remain_fixed(self):
        movable_slot = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 8, 6, 8, 30),
            plan_end=datetime(2026, 8, 6, 12, 30),
            status="scheduled",
            tier="confirmed",
        )
        frozen_slot = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 8, 6, 13, 30),
            plan_end=datetime(2026, 8, 6, 20, 0),
            status="scheduled",
            tier="frozen",
        )
        self.db.add_all([movable_slot, frozen_slot])
        self.db.commit()

        fixed_slots = load_fixed_slots(self.db, excluded_task_ids={1})

        self.assertEqual([frozen_slot.id], [slot.id for slot in fixed_slots])

    def test_future_running_frozen_slot_is_still_fixed(self):
        frozen_slot = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=datetime.now() + timedelta(days=1),
            plan_end=datetime.now() + timedelta(days=1, hours=4),
            status="running",
            tier="frozen",
        )
        self.db.add(frozen_slot)
        self.db.commit()

        self.assertEqual([frozen_slot.id], [slot.id for slot in load_fixed_slots(self.db)])

    def test_unstarted_running_continuation_is_not_fixed(self):
        future_slot = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=datetime.now() + timedelta(days=1),
            plan_end=datetime.now() + timedelta(days=1, hours=1, minutes=30),
            status="running",
            tier="confirmed",
        )
        self.db.add(future_slot)
        self.db.commit()

        self.assertEqual([], load_fixed_slots(self.db))

    def test_replan_supersedes_unstarted_running_continuation(self):
        continuation = TimeSlot(
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 8, 27, 8, 30),
            plan_end=datetime(2026, 8, 27, 20, 0),
            status="running",
            tier="confirmed",
        )
        self.db.add(continuation)
        self.db.commit()

        supersede_replaceable_slots(
            self.db, {continuation.task_id}, "测试重排", continuation.plan_start,
        )

        self.assertEqual("superseded", continuation.lifecycle_status)
        self.assertEqual("测试重排", continuation.superseded_reason)

    def test_only_slots_for_relevant_resources_are_loaded(self):
        project = Project(code="P1", name="测试项目", priority=3)
        self.db.add(project)
        self.db.flush()
        relevant_manual_task = Task(
            project_id=project.id,
            name="相关人工任务",
            task_type="manual",
            requires_instrument=False,
            requires_human=True,
            assignee_id=7,
        )
        unrelated_task = Task(
            project_id=project.id,
            name="无关仪器任务",
            task_type="instrument",
            requires_instrument=True,
            requires_human=True,
            assignee_id=8,
        )
        self.db.add_all([relevant_manual_task, unrelated_task])
        self.db.flush()
        relevant_manual_slot = TimeSlot(
            task_id=relevant_manual_task.id,
            plan_start=datetime(2026, 8, 3, 8, 30),
            plan_end=datetime(2026, 8, 3, 12, 30),
            status="scheduled",
        )
        unrelated_slot = TimeSlot(
            task_id=unrelated_task.id,
            instrument_id=2,
            plan_start=datetime(2026, 8, 3, 8, 30),
            plan_end=datetime(2026, 8, 3, 20, 0),
            status="running",
        )
        self.db.add_all([relevant_manual_slot, unrelated_slot])
        self.db.commit()

        fixed_slots = load_fixed_slots(
            self.db,
            relevant_instrument_ids={1},
            relevant_assignee_ids={7},
        )

        self.assertEqual(
            [relevant_manual_slot.id],
            [slot.id for slot in fixed_slots],
        )

    def test_running_fixed_slot_uses_actual_start(self):
        model = cp_model.CpModel()
        completed = TimeSlot(
            id=10,
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 7, 20, 8, 30),
            plan_end=datetime(2026, 7, 20, 12, 0),
            actual_start=datetime(2026, 7, 20, 8, 30),
            actual_end=datetime(2026, 7, 20, 14, 5),
            status="completed",
        )
        running = TimeSlot(
            id=11,
            task_id=2,
            instrument_id=1,
            plan_start=datetime(2026, 7, 20, 12, 30),
            plan_end=datetime(2026, 7, 21, 6, 0),
            actual_start=datetime(2026, 7, 20, 14, 17),
            status="running",
        )

        add_instrument_capacity_constraints(
            model=model,
            instruments=[SimpleNamespace(id=1)],
            tasks=[],
            capacity_intervals={},
            presences={},
            inst_starts={},
            inst_ends={},
            split_unit_presences={},
            fixed_slots=[completed, running],
            horizon_start=datetime(2026, 7, 20, 8, 30),
            total_units=48,
            non_overlap_enabled=True,
            setup_units=0,
        )

        solver = cp_model.CpSolver()
        self.assertIn(solver.Solve(model), (cp_model.OPTIMAL, cp_model.FEASIBLE))

    def test_overlapping_segments_of_same_running_task_are_merged(self):
        model = cp_model.CpModel()
        running = TimeSlot(
            id=10,
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 7, 16, 8, 30),
            plan_end=datetime(2026, 7, 16, 22, 0),
            actual_start=datetime(2026, 7, 16, 9, 30),
            status="running",
        )
        continuation = TimeSlot(
            id=11,
            task_id=1,
            instrument_id=1,
            plan_start=datetime(2026, 7, 17, 8, 30),
            plan_end=datetime(2026, 7, 17, 22, 0),
            status="running",
        )

        add_instrument_capacity_constraints(
            model=model,
            instruments=[SimpleNamespace(id=1)],
            tasks=[],
            capacity_intervals={},
            presences={},
            inst_starts={},
            inst_ends={},
            split_unit_presences={},
            fixed_slots=[running, continuation],
            horizon_start=datetime(2026, 7, 16, 8, 30),
            total_units=240,
            non_overlap_enabled=True,
            setup_units=0,
        )

        solver = cp_model.CpSolver()
        self.assertIn(solver.Solve(model), (cp_model.OPTIMAL, cp_model.FEASIBLE))

    def test_early_started_future_slot_does_not_block_until_plan_end(self):
        model = cp_model.CpModel()
        horizon_start = datetime.now().replace(second=0, microsecond=0)
        current_slot = TimeSlot(
            id=10,
            task_id=1,
            instrument_id=1,
            plan_start=horizon_start + timedelta(days=7),
            plan_end=horizon_start + timedelta(days=7, hours=3),
            actual_start=horizon_start - timedelta(hours=1),
            status="running",
        )
        available_slot = TimeSlot(
            id=11,
            task_id=2,
            instrument_id=1,
            plan_start=horizon_start + timedelta(days=1),
            plan_end=horizon_start + timedelta(days=1, hours=3),
            status="scheduled",
        )

        add_instrument_capacity_constraints(
            model=model,
            instruments=[SimpleNamespace(id=1)],
            tasks=[],
            capacity_intervals={},
            presences={},
            inst_starts={},
            inst_ends={},
            split_unit_presences={},
            fixed_slots=[current_slot, available_slot],
            horizon_start=horizon_start,
            total_units=24 * 30,
            non_overlap_enabled=True,
            setup_units=0,
        )

        solver = cp_model.CpSolver()
        self.assertIn(solver.Solve(model), (cp_model.OPTIMAL, cp_model.FEASIBLE))

    def test_human_tasks_for_same_assignee_cannot_overlap(self):
        model = cp_model.CpModel()
        first_start = model.NewIntVar(0, 2, "first_start")
        first_end = model.NewIntVar(2, 4, "first_end")
        second_start = model.NewIntVar(0, 2, "second_start")
        second_end = model.NewIntVar(2, 4, "second_end")
        first_interval = model.NewIntervalVar(first_start, 2, first_end, "first")
        second_interval = model.NewIntervalVar(second_start, 2, second_end, "second")
        tasks = [
            SimpleNamespace(id=1, requires_human=True, assignee_id=1),
            SimpleNamespace(id=2, requires_human=True, assignee_id=1),
        ]

        add_human_capacity_constraints(
            model,
            tasks,
            {1: first_interval, 2: second_interval},
            [],
            datetime(2026, 7, 14),
            4,
        )
        solver = cp_model.CpSolver()

        self.assertIn(solver.Solve(model), (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertTrue(
            solver.Value(first_end) <= solver.Value(second_start)
            or solver.Value(second_end) <= solver.Value(first_start)
        )

    def test_new_task_cannot_jump_a_frozen_instrument_slot(self):
        model = cp_model.CpModel()
        start = model.NewIntVar(0, 9, "start")
        end = model.NewIntVar(1, 10, "end")
        presence = model.NewBoolVar("presence")
        model.Add(presence == 1)
        model.Add(end == start + 1)
        interval = model.NewOptionalIntervalVar(start, 1, end, presence, "task")
        frozen_slot = TimeSlot(
            id=10,
            task_id=20,
            instrument_id=1,
            plan_start=datetime(2026, 7, 14, 9, 30),
            plan_end=datetime(2026, 7, 14, 10, 30),
            tier="frozen",
            status="scheduled",
        )

        add_instrument_capacity_constraints(
            model=model,
            instruments=[SimpleNamespace(id=1)],
            tasks=[SimpleNamespace(id=1, allow_split=False)],
            capacity_intervals={1: [interval]},
            presences={(1, 1): presence},
            inst_starts={(1, 1): start},
            inst_ends={(1, 1): end},
            split_unit_presences={},
            fixed_slots=[frozen_slot],
            horizon_start=datetime(2026, 7, 14, 8, 30),
            total_units=10,
            non_overlap_enabled=True,
            setup_units=0,
        )
        model.Minimize(start)
        solver = cp_model.CpSolver()

        self.assertEqual(cp_model.OPTIMAL, solver.Solve(model))
        self.assertEqual(4, solver.Value(start))


if __name__ == "__main__":
    unittest.main()

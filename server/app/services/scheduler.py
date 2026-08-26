import logging

from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from uuid import uuid4
from ortools.sat.python import cp_model
from app.models import Task, TimeSlot
from app.core.config import get_settings
from app.services.schedule_rule_service import get_solver_constraints
from app.services.schedule_conflict_service import (
    ScheduleConflictError,
    ensure_no_dependency_conflicts,
    ensure_no_human_conflicts,
    ensure_no_instrument_conflicts,
)
from app.services.scheduler_fixed_slots import (
    add_human_capacity_constraints,
    add_instrument_capacity_constraints,
    load_fixed_bridge_reservations,
    load_fixed_slots,
)
from app.services.scheduler_persistence import persist_slots
from app.services.schedule_slot_change_log_service import supersede_slot
from app.services.scheduler_objective import add_scheduler_objective
from app.services.scheduler_split_tasks import add_split_task_variables
from app.services.scheduler_instrument_bridging import add_instrument_bridge_intervals
from app.services.schedule_deadline_recommendation_job_service import (
    create_deadline_recommendation_job,
)
from app.services.scheduler_diagnostics import (
    log_solver_failure_snapshot,
    schedule_infeasibility_diagnostic,
    schedule_infeasibility_message,
    unavailable_instrument_message,
)
from app.services.scheduler_data import (
    load_scheduler_data,
    load_task_children,
    load_diagnostic_resource_tasks,
)
from app.services.project_hours_validation_service import (
    ProjectHoursExceededError,
    validate_projects_estimated_hours,
)
from app.services.scheduler_helpers import (
    build_compatibility,
    build_dependencies,
    build_maintenance_windows,
    build_working_prefix_sum,
    datetime_to_units,
    time_horizon,
    TIME_UNIT_MINUTES,
    to_units,
)
from app.services.approval_gate_service import unapproved_gate_context
from app.services.schedule_advance_notification_service import (
    capture_task_schedule_windows,
    notify_rescheduled_tasks_delayed,
    notify_rescheduled_tasks_advanced,
)
from app.services.calendar_service import ensure_calendar_range
from app.services.schedule_calendar_snapshot_service import save_schedule_calendar_snapshot
from app.services.scheduler_solver_trace_service import SolverTrace
from app.services.scheduler_failure_diagnostics import build_task_window_failure
from app.services.scheduler_predecessor_bounds import load_missing_predecessor_ends
from app.services.instrument_working_time_service import (
    load_working_time_context,
    serialize_instrument_policies,
)


_logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, db: Session):
        self.db = db

    def generate(
        self,
        project_ids: Optional[List[int]] = None,
        mode: str = "normal",
        task_ids: Optional[List[int]] = None,
        commit: bool = True,
        excluded_task_ids: set[int] | None = None,
        original_schedule_windows: dict[int, tuple[datetime, datetime]] | None = None,
        stability_task_ids: set[int] | None = None,
        additional_dependencies: list[tuple[int, int]] | None = None,
        earliest_start_bounds: dict[int, datetime] | None = None,
        relaxed_project_end_task_ids: set[int] | None = None,
        advance_notification_reason: str = "重新排程",
        emit_advance_notifications: bool = True,
        early_start_task_ids: set[int] | None = None,
        current_project_id: int | None = None,
        rollback_on_conflict: bool = True,
        include_failure_diagnostics: bool = True,
        solver_time_limit: float = 30.0,
        remaining_duration_minutes: dict[int, int] | None = None,
        replaceable_task_ids: set[int] | None = None,
    ) -> dict:
        if current_project_id is None:
            return {"status": "error", "message": "排程请求缺少当前项目ID"}
        tasks, instruments = load_scheduler_data(
            self.db,
            project_ids,
            task_ids,
            excluded_task_ids,
            replaceable_task_ids,
        )
        if not tasks:
            return {"status": "ok", "message": "没有待排仪器任务", "timeslots_created": 0}
        # Resource release is a first-class priority: once hard constraints
        # allow a task to start, avoid unexplained idle gaps before it.
        early_start_task_ids = early_start_task_ids or {task.id for task in tasks}
        if original_schedule_windows is None:
            original_schedule_windows = capture_task_schedule_windows(
                self.db,
                {task.id for task in tasks},
            )
        unassigned_human_tasks = [
            task for task in tasks if task.requires_human and not task.assignee_id
        ]
        if unassigned_human_tasks:
            names = "、".join(task.name for task in unassigned_human_tasks[:3])
            suffix = "等" if len(unassigned_human_tasks) > 3 else ""
            return {
                "status": "error",
                "message": f"排程失败：人工任务【{names}{suffix}】未指定负责人。",
            }
        try:
            validate_projects_estimated_hours(self.db, {current_project_id})
        except ProjectHoursExceededError as exc:
            return {"status": "error", "message": str(exc)}
        if not instruments:
            return {"status": "error", "message": "没有可用仪器"}

        constraints = get_solver_constraints(self.db)
        horizon_start, horizon_end, total_units = time_horizon()
        ensure_calendar_range(self.db, horizon_start.date(), horizon_end.date())
        approval_bounds, forecast_task_ids = unapproved_gate_context(self.db, tasks)
        if earliest_start_bounds:
            for task_id, bound in earliest_start_bounds.items():
                current = approval_bounds.get(task_id)
                if current is None or bound > current:
                    approval_bounds[task_id] = bound

        freezing_rule = constraints["freezing"]
        freeze_days = (freezing_rule.params or {}).get(
            "freeze_days",
            get_settings().FROZEN_DAYS,
        )

        compat = build_compatibility(
            tasks,
            instruments,
            constraints["capability_matching"].is_enabled,
        )
        diagnostic_message = unavailable_instrument_message(self.db, tasks, compat)
        if diagnostic_message:
            return {"status": "error", "message": diagnostic_message}

        task_children = load_task_children(self.db, tasks)
        business_task_deps = sorted(set(build_dependencies(tasks, task_children)))
        queue_task_deps = sorted(set(additional_dependencies or []))
        task_deps = sorted(set(business_task_deps) | set(queue_task_deps))
        maintenance_rule = constraints["maintenance_avoidance"]
        maint_windows = (
            build_maintenance_windows(instruments, horizon_start)
            if maintenance_rule.is_enabled
            else []
        )
        working_rule = constraints["working_hours"]
        working_params = working_rule.params or {}
        working_context = load_working_time_context(
            self.db, horizon_start, horizon_end, instruments,
        )
        global_policy = working_context.global_policy
        day_start_minutes = global_policy.day_start_minutes
        day_end_minutes = global_policy.day_end_minutes
        include_weekends = global_policy.include_weekends
        include_holidays = global_policy.include_holidays
        calendar_days = working_context.calendar_days
        global_prefix_sum = build_working_prefix_sum(
            horizon_start,
            total_units,
            day_start_minutes,
            day_end_minutes,
            [],
            calendar_days,
            include_weekends,
            include_holidays,
        )
        instrument_prefix_sums = {
            instrument.id: build_working_prefix_sum(
                horizon_start,
                total_units,
                working_context.policy_for(instrument.id).day_start_minutes,
                working_context.policy_for(instrument.id).day_end_minutes,
                [window for window in maint_windows if window[0] == instrument.id],
                calendar_days,
                include_weekends,
                include_holidays,
            )
            for instrument in instruments
        }

        relevant_instrument_ids = {
            instrument.id
            for task in tasks
            for instrument in compat.get(task.id, [])
        }
        relevant_assignee_ids = {
            task.assignee_id
            for task in tasks
            if task.requires_human and task.assignee_id is not None
        }
        fixed_slots = load_fixed_slots(
            self.db,
            {task.id for task in tasks},
            relevant_instrument_ids,
            relevant_assignee_ids,
        )
        fixed_bridge_reservations = load_fixed_bridge_reservations(
            self.db,
            {task.id for task in tasks},
            relevant_instrument_ids,
        )

        model = cp_model.CpModel()

        # Decision variables
        capacity_intervals: Dict[int, list[cp_model.IntervalVar]] = {}
        presences: Dict[Tuple[int, int], cp_model.IntVar] = {}
        inst_starts: Dict[Tuple[int, int], cp_model.IntVar] = {}
        inst_ends: Dict[Tuple[int, int], cp_model.IntVar] = {}
        task_starts: Dict[int, cp_model.IntVar] = {}
        task_ends: Dict[int, cp_model.IntVar] = {}
        task_intervals: Dict[int, cp_model.IntervalVar] = {}
        task_tardiness: Dict[int, cp_model.IntVar] = {}
        split_unit_presences: Dict[Tuple[int, int, int], cp_model.IntVar] = {}
        for instrument in instruments:
            capacity_intervals[instrument.id] = []

        for t in tasks:
            dur = to_units(t.est_duration_hours or 4)
            if t.switchover_hours and t.switchover_hours > 0:
                dur += to_units(t.switchover_hours)
            dur = _remaining_duration_units(
                t,
                dur,
                fixed_slots,
                global_prefix_sum,
                instrument_prefix_sums,
                horizon_start,
                total_units,
                remaining_duration_minutes,
            )

            # Compute project-level hard constraint window
            p_start_unit = 0
            p_end_unit = total_units
            if t.project and constraints["project_window"].is_enabled:
                if t.project.start_date:
                    p_start_u = datetime_to_units(t.project.start_date, horizon_start)
                    p_start_unit = max(0, p_start_u)
                if t.project.end_date:
                    p_end_u = datetime_to_units(t.project.end_date, horizon_start)
                    p_end_unit = min(total_units, p_end_u)
            approval_bound = approval_bounds.get(t.id)
            if approval_bound:
                p_start_unit = max(
                    p_start_unit,
                    datetime_to_units(approval_bound, horizon_start),
                )

            # Guard: task duration exceeds available project window
            if p_start_unit + dur > p_end_unit:
                earliest_start = horizon_start + timedelta(
                    minutes=p_start_unit * TIME_UNIT_MINUTES,
                )
                return {
                    "status": "error",
                    **build_task_window_failure(t, earliest_start, dur),
                }

            # Constrain task start/end within project boundaries
            task_start_max = p_end_unit - dur
            task_end_min = p_start_unit + dur
            task_starts[t.id] = model.NewIntVar(
                p_start_unit,
                task_start_max,
                f"start_t{t.id}",
            )
            task_ends[t.id] = model.NewIntVar(
                task_end_min,
                p_end_unit,
                f"end_t{t.id}",
            )
            task_tardiness[t.id] = model.NewIntVar(0, total_units, f"tardy_t{t.id}")

            # Physical span can stretch beyond dur to accommodate night breaks
            task_span = model.NewIntVar(
                dur,
                p_end_unit - p_start_unit,
                f"span_t{t.id}",
            )
            model.Add(task_ends[t.id] - task_starts[t.id] == task_span)

            task_interval = model.NewIntervalVar(
                task_starts[t.id], task_span, task_ends[t.id], f"task_iv_t{t.id}"
            )
            task_intervals[t.id] = task_interval

            candidates = compat.get(t.id, [])
            if not candidates:
                # Manual task: apply prefix-sum constraint to respect night window
                start_work_acc = model.NewIntVar(0, total_units, f"start_acc_t{t.id}")
                end_work_acc = model.NewIntVar(0, total_units, f"end_acc_t{t.id}")
                model.AddElement(task_starts[t.id], global_prefix_sum, start_work_acc)
                model.AddElement(task_ends[t.id], global_prefix_sum, end_work_acc)
                model.Add(end_work_acc - start_work_acc == dur)
                continue

            if t.allow_split:
                is_valid_split = add_split_task_variables(
                    model,
                    t,
                    candidates,
                    dur,
                    p_start_unit,
                    p_end_unit,
                    task_start_max,
                    task_end_min,
                    total_units,
                    instrument_prefix_sums,
                    task_starts[t.id],
                    task_ends[t.id],
                    presences,
                    inst_starts,
                    inst_ends,
                    capacity_intervals,
                    split_unit_presences,
                )
                if not is_valid_split:
                    return {"status": "error", "message": f"排程失败：任务【{t.name}】没有足够的可拆分工作时段。"}
                continue

            alt_starts = []
            alt_ends = []
            alt_presences = []
            for inst in candidates:
                instrument_prefix_sum = instrument_prefix_sums[inst.id]
                key = (t.id, inst.id)
                presences[key] = model.NewBoolVar(f"presence_t{t.id}_i{inst.id}")
                inst_start = model.NewIntVarFromDomain(
                    _optional_time_domain(p_start_unit, task_start_max),
                    f"start_t{t.id}_i{inst.id}",
                )
                inst_end = model.NewIntVarFromDomain(
                    _optional_time_domain(task_end_min, p_end_unit),
                    f"end_t{t.id}_i{inst.id}",
                )
                inst_span = model.NewIntVar(
                    0,
                    p_end_unit - p_start_unit,
                    f"span_t{t.id}_i{inst.id}",
                )
                model.Add(inst_end - inst_start == inst_span)

                inst_iv = model.NewOptionalIntervalVar(
                    inst_start, inst_span, inst_end, presences[key], f"iv_t{t.id}_i{inst.id}"
                )
                capacity_intervals[inst.id].append(inst_iv)
                alt_starts.append(inst_start)
                alt_ends.append(inst_end)
                alt_presences.append(presences[key])

                # Store per-instrument start/end for cross-project constraints
                inst_starts[key] = inst_start
                inst_ends[key] = inst_end

                # Bidirectional link: per-instrument start/end == task start/end
                model.Add(task_starts[t.id] == inst_start).OnlyEnforceIf(presences[key])
                model.Add(task_ends[t.id] == inst_end).OnlyEnforceIf(presences[key])
                # When not selected, force instrument start/end to zero
                model.Add(inst_start == 0).OnlyEnforceIf(presences[key].Not())
                model.Add(inst_end == 0).OnlyEnforceIf(presences[key].Not())

                # Prefix-sum constraint: effective working time within span == dur
                start_work_acc = model.NewIntVar(0, total_units, f"start_acc_t{t.id}_i{inst.id}")
                end_work_acc = model.NewIntVar(0, total_units, f"end_acc_t{t.id}_i{inst.id}")
                model.AddElement(inst_start, instrument_prefix_sum, start_work_acc)
                model.AddElement(inst_end, instrument_prefix_sum, end_work_acc)
                model.Add(end_work_acc - start_work_acc == dur).OnlyEnforceIf(presences[key])

            # Exactly one instrument assigned
            model.AddExactlyOne(alt_presences)
        # === Cross-project switching: setup time + penalty ===
        setup_rule = constraints["cross_project_setup"]
        setup_hours = (
            (setup_rule.params or {}).get("setup_hours", 0.5)
            if setup_rule.is_enabled
            else 0
        )
        CROSS_PROJECT_SETUP_UNITS = to_units(setup_hours) if setup_hours else 0
        instrument_bridges = add_instrument_bridge_intervals(
            model,
            tasks,
            task_deps,
            compat,
            task_starts,
            task_ends,
            capacity_intervals,
            presences,
            total_units,
        )
        add_instrument_capacity_constraints(
            model,
            instruments,
            tasks,
            capacity_intervals,
            presences,
            inst_starts,
            inst_ends,
            split_unit_presences,
            fixed_slots,
            horizon_start,
            total_units,
            constraints["non_overlap"].is_enabled,
            CROSS_PROJECT_SETUP_UNITS,
            fixed_bridge_reservations,
        )
        add_human_capacity_constraints(
            model,
            tasks,
            task_intervals,
            fixed_slots,
            horizon_start,
            total_units,
        )
        switch_penalties = []
        tasks_by_id = {t.id: t for t in tasks}

        for inst in instruments:
            inst_task_ids = [key[0] for key in presences if key[1] == inst.id]
            for i in range(len(inst_task_ids)):
                for j in range(i + 1, len(inst_task_ids)):
                    tA_id = inst_task_ids[i]
                    tB_id = inst_task_ids[j]
                    tA = tasks_by_id[tA_id]
                    tB = tasks_by_id[tB_id]

                    if tA.project_id == tB.project_id:
                        continue

                    pA = presences[(tA_id, inst.id)]
                    pB = presences[(tB_id, inst.id)]
                    startA, endA = inst_starts[(tA_id, inst.id)], inst_ends[(tA_id, inst.id)]
                    startB, endB = inst_starts[(tB_id, inst.id)], inst_ends[(tB_id, inst.id)]

                    a_before_b = model.NewBoolVar(f"seq_{tA_id}_before_{tB_id}_on_{inst.id}")
                    b_before_a = model.NewBoolVar(f"seq_{tB_id}_before_{tA_id}_on_{inst.id}")

                    # Ordering: when both present, exactly one precedes the other
                    model.Add(a_before_b + b_before_a == 1).OnlyEnforceIf([pA, pB])
                    # When not co-present, force ordering vars to 0
                    model.Add(a_before_b == 0).OnlyEnforceIf(pA.Not())
                    model.Add(b_before_a == 0).OnlyEnforceIf(pA.Not())
                    model.Add(a_before_b == 0).OnlyEnforceIf(pB.Not())
                    model.Add(b_before_a == 0).OnlyEnforceIf(pB.Not())

                    # Setup time between cross-project tasks
                    model.Add(startB >= endA + CROSS_PROJECT_SETUP_UNITS).OnlyEnforceIf([pA, pB, a_before_b])
                    model.Add(startA >= endB + CROSS_PROJECT_SETUP_UNITS).OnlyEnforceIf([pA, pB, b_before_a])

                    # Collect cross-project co-presence for penalty
                    both_present = model.NewBoolVar(f"both_{tA_id}_{tB_id}_on_{inst.id}")

                    # Proper AND: both_present = pA AND pB
                    model.AddImplication(both_present, pA)
                    model.AddImplication(both_present, pB)
                    model.AddBoolOr([pA.Not(), pB.Not()]).OnlyEnforceIf(both_present.Not())

                    switch_penalties.append(both_present)

        # Precedence constraints (DAG)
        # Bug 1 fix: handle frozen/missing predecessors as constant bounds
        missing_pred_ids = {
            pred_id
            for _, pred_id in task_deps
            if pred_id not in task_starts
        }

        missing_pred_ends = load_missing_predecessor_ends(
            self.db, missing_pred_ids, horizon_start,
        )

        if constraints["precedence"].is_enabled:
            for tid, pred_id in task_deps:
                if pred_id in task_starts and tid in task_starts:
                    model.Add(task_starts[tid] >= task_ends[pred_id])

            # Frozen/missing predecessors: apply constant lower-bound
            for task_id, predecessor_id in task_deps:
                if (
                    predecessor_id in missing_pred_ends
                    and task_id in task_starts
                ):
                    model.Add(
                        task_starts[task_id]
                        >= missing_pred_ends[predecessor_id]
                    )

        # Keep dependent work close together when resources allow it. This is
        # a soft objective: precedence and frozen/resource constraints remain
        # authoritative, while unexplained multi-day gaps are discouraged.
        dependency_gap_penalties = []
        for task_id, predecessor_id in task_deps:
            if task_id not in task_starts or predecessor_id not in task_ends:
                continue
            gap = model.NewIntVar(0, total_units, f"dependency_gap_{predecessor_id}_{task_id}")
            model.Add(gap >= task_starts[task_id] - task_ends[predecessor_id])
            dependency_gap_penalties.append(gap)

        early_start_penalties = [
            task_starts[task_id]
            for task_id in (early_start_task_ids or set())
            if task_id in task_starts
        ]

        # === Project split penalty: discourage spreading one project across many instruments ===
        project_to_tasks = {}
        for t in tasks:
            if t.requires_instrument and t.project_id:
                if t.project_id not in project_to_tasks:
                    project_to_tasks[t.project_id] = []
                project_to_tasks[t.project_id].append(t)

        project_inst_used_vars = []
        for pid, p_tasks in project_to_tasks.items():
            for inst in instruments:
                used_var = model.NewBoolVar(f"used_p{pid}_i{inst.id}")
                task_presences = []
                for t in p_tasks:
                    key = (t.id, inst.id)
                    if key in presences:
                        task_presences.append(presences[key])
                if task_presences:
                    model.AddMaxEquality(used_var, task_presences)
                    project_inst_used_vars.append(used_var)

        # Milestone deadlines → tardiness
        for t in tasks:
            if (
                constraints["milestone_deadline"].is_enabled
                and t.milestone_id
                and t.milestone
            ):
                deadline = datetime_to_units(t.milestone.due_date, horizon_start)
                if 0 <= deadline <= total_units:
                    model.Add(task_tardiness[t.id] >= task_ends[t.id] - deadline)

        stability_penalties = []
        for task_id in stability_task_ids or set():
            old_window = original_schedule_windows.get(task_id)
            if task_id not in task_starts or not old_window:
                continue
            old_start_unit = max(0, min(
                total_units,
                datetime_to_units(old_window[0], horizon_start),
            ))
            deviation = model.NewIntVar(0, total_units, f"stability_t{task_id}")
            model.AddAbsEquality(deviation, task_starts[task_id] - old_start_unit)
            stability_penalties.append(deviation)

        add_scheduler_objective(
            model,
            tasks,
            task_starts,
            task_ends,
            task_tardiness,
            switch_penalties,
            project_inst_used_vars,
            total_units,
            _sibling_cohesion_weight(constraints["sibling_task_cohesion"]),
            {
                parent_id: len(child_ids)
                for parent_id, child_ids in task_children.items()
            },
            stability_penalties,
            dependency_gap_penalties,
            early_start_penalties,
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = solver_time_limit
        solver.parameters.num_search_workers = 4
        solver.parameters.log_search_progress = True
        solver.parameters.log_to_stdout = False
        solver_trace = SolverTrace(
            current_project_id, len(tasks), mode, solver_time_limit,
        )
        solver_trace.write_model(model)
        solver.log_callback = solver_trace.write
        status = solver.Solve(model)
        elapsed_ms = solver_trace.finish(solver, solver.StatusName(status))
        _logger.info(
            "scheduler_solve project_id=%s tasks=%s mode=%s status=%s elapsed_ms=%s trace=%s",
            current_project_id,
            len(tasks),
            mode,
            solver.StatusName(status),
            elapsed_ms,
            solver_trace.path,
        )

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if not include_failure_diagnostics:
                return {
                    "status": "error",
                    "message": "未找到可行排程",
                    "solver_status": solver.StatusName(status),
                }
            log_solver_failure_snapshot(
                tasks,
                compat,
                task_deps,
                missing_pred_ends,
                fixed_slots,
                instrument_prefix_sums,
                horizon_start,
                total_units,
                status,
            )
            try:
                diagnostic_tasks = tasks + load_diagnostic_resource_tasks(
                    self.db,
                    {task.id for task in tasks},
                    current_project_id=current_project_id,
                )
                diagnostic_compat = build_compatibility(
                    diagnostic_tasks,
                    instruments,
                    constraints["capability_matching"].is_enabled,
                )
                diagnostic = schedule_infeasibility_diagnostic(
                    diagnostic_tasks,
                    task_deps,
                    missing_pred_ends,
                    diagnostic_compat,
                    global_prefix_sum,
                    instrument_prefix_sums,
                    horizon_start,
                    total_units,
                    current_project_id=current_project_id,
                    excluded_task_ids=relaxed_project_end_task_ids,
                )
                diagnostic_message = diagnostic["message"]
                current_deadline = next(
                    task.project.end_date for task in tasks
                    if task.project_id == current_project_id
                )
                job = create_deadline_recommendation_job(
                    current_project_id,
                    [task.id for task in tasks],
                    current_deadline,
                    horizon_start,
                    horizon_end,
                    instrument_prefix_sums,
                    diagnostic["schedule_failure"],
                    {
                        "project_ids": project_ids,
                        "mode": mode,
                        "task_ids": task_ids,
                        "excluded_task_ids": excluded_task_ids,
                        "additional_dependencies": additional_dependencies,
                        "earliest_start_bounds": earliest_start_bounds,
                        "relaxed_project_end_task_ids": relaxed_project_end_task_ids,
                        "early_start_task_ids": early_start_task_ids,
                        "rollback_on_conflict": False,
                    },
                )
                if job:
                    diagnostic["schedule_failure"]["recommendation_job"] = job
            except Exception as exc:
                diagnostic_message = f"排程诊断失败：{exc}"
            response = {
                "status": "error",
                "message": diagnostic_message,
            }
            if 'diagnostic' in locals() and isinstance(diagnostic, dict):
                response["schedule_failure"] = diagnostic.get("schedule_failure")
            return response

        # Persist results
        _supersede_replaceable_slots(
            self.db,
            replaceable_task_ids or set(),
            "CP-SAT局部重排",
        )
        schedule_run_id = _new_schedule_run_id()
        save_schedule_calendar_snapshot(
            self.db,
            schedule_run_id,
            horizon_start,
            horizon_end,
            working_params,
            calendar_days,
            maint_windows,
            serialize_instrument_policies(working_context),
        )
        created = persist_slots(
            self.db,
            tasks,
            instruments,
            solver,
            task_starts,
            task_ends,
            presences,
            horizon_start,
            working_context,
            freeze_days,
            schedule_run_id,
            commit=False,
            split_unit_presences=split_unit_presences,
            forecast_task_ids=forecast_task_ids,
            instrument_bridges=instrument_bridges,
        )

        try:
            ensure_no_instrument_conflicts(self.db, schedule_run_id)
            ensure_no_human_conflicts(self.db, schedule_run_id)
            ensure_no_dependency_conflicts(self.db, business_task_deps, schedule_run_id)
            ensure_no_dependency_conflicts(
                self.db,
                queue_task_deps,
                schedule_run_id,
                task_slots_from_run_only=True,
            )
        except ScheduleConflictError as exc:
            if rollback_on_conflict:
                self.db.rollback()
            return {"status": "error", "message": str(exc), "timeslots_created": 0}
        if emit_advance_notifications:
            notify_rescheduled_tasks_advanced(
                self.db,
                original_schedule_windows,
                advance_notification_reason,
            )
            notify_rescheduled_tasks_delayed(
                self.db,
                original_schedule_windows,
                advance_notification_reason,
            )
        if commit:
            self.db.commit()

        return {
            "status": "ok",
            "message": f"排程完成",
            "timeslots_created": created,
            "schedule_run_id": schedule_run_id,
            "solver_status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "objective_value": int(solver.ObjectiveValue()),
        }

def _new_schedule_run_id() -> str:
    return f"{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:8]}"


def _sibling_cohesion_weight(rule) -> int:
    if not rule.is_enabled:
        return 0
    raw_weight = (rule.params or {}).get("weight", 1.0)
    try:
        weight = float(raw_weight)
    except (TypeError, ValueError):
        weight = 1.0
    return round(max(0.0, min(10.0, weight)) * 100)


def _optional_time_domain(lower_bound: int, upper_bound: int) -> cp_model.Domain:
    if lower_bound == 0:
        return cp_model.Domain.FromIntervals([(0, upper_bound)])
    return cp_model.Domain.FromIntervals([
        (0, 0),
        (lower_bound, upper_bound),
    ])


def _remaining_duration_units(
    task,
    duration_units: int,
    fixed_slots,
    global_prefix_sum,
    instrument_prefix_sums,
    horizon_start,
    total_units: int,
    remaining_duration_minutes: dict[int, int] | None = None,
) -> int:
    from app.services.task_progress_service import planned_task_minutes

    if remaining_duration_minutes and task.id in remaining_duration_minutes:
        return max(1, to_units(remaining_duration_minutes[task.id] / 60))
    planned_minutes = planned_task_minutes(task)
    executed_minutes = int(getattr(task, "executed_minutes", 0) or 0)
    if hasattr(task, "executed_minutes"):
        return max(1, to_units(planned_minutes / 60) - to_units(executed_minutes / 60))
    segments = list(getattr(task, "execution_segments", []) or [])
    fixed_units = _executed_duration_units(
        segments,
        task,
        global_prefix_sum,
        instrument_prefix_sums,
        horizon_start,
        total_units,
    )
    if not segments:
        fixed_units = _executed_slot_duration_units(
            task,
            fixed_slots,
            global_prefix_sum,
            instrument_prefix_sums,
            horizon_start,
            total_units,
        )
    return max(1, duration_units - fixed_units)


def _supersede_replaceable_slots(db, task_ids: set[int], reason: str) -> None:
    if not task_ids:
        return
    slots = db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
        TimeSlot.status == "scheduled",
        TimeSlot.actual_start.is_(None),
        TimeSlot.actual_end.is_(None),
        TimeSlot.tier != "frozen",
    ).all()
    for slot in slots:
        supersede_slot(db, slot, reason)
    db.flush()


def _executed_duration_units(
    segments,
    task,
    global_prefix_sum,
    instrument_prefix_sums,
    horizon_start,
    total_units,
) -> int:
    total = 0
    instrument_id = next(
        (getattr(segment, "instrument_id", None) for segment in segments
         if getattr(segment, "instrument_id", None) is not None),
        None,
    )
    prefix_sum = instrument_prefix_sums.get(instrument_id) if instrument_id else global_prefix_sum
    if not prefix_sum:
        return 0
    for segment in segments:
        start = max(0, datetime_to_units(segment.started_at, horizon_start))
        end_time = segment.ended_at or datetime.now()
        end = min(total_units, datetime_to_units(end_time, horizon_start))
        if end > start:
            total += prefix_sum[end] - prefix_sum[start]
    return total


def _executed_slot_duration_units(
    task,
    fixed_slots,
    global_prefix_sum,
    instrument_prefix_sums,
    horizon_start,
    total_units,
) -> int:
    total = 0
    for slot in fixed_slots:
        if slot.task_id != task.id or not slot.actual_start:
            continue
        start = max(0, datetime_to_units(slot.actual_start, horizon_start))
        end_time = slot.actual_end or datetime.now()
        end = min(total_units, datetime_to_units(end_time, horizon_start))
        prefix_sum = (
            instrument_prefix_sums.get(slot.instrument_id)
            if slot.instrument_id is not None else global_prefix_sum
        )
        if prefix_sum and end > start:
            total += prefix_sum[end] - prefix_sum[start]
    return total

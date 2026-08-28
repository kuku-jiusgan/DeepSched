import logging

from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from ortools.sat.python import cp_model
from app.core.config import get_settings
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_fixed_slots import (
    add_human_capacity_constraints,
    add_instrument_capacity_constraints,
    load_fixed_bridge_reservations,
    load_fixed_slots,
)
from app.services.scheduler_objective import add_scheduler_objective
from app.services.scheduler_instrument_bridging import add_instrument_bridge_intervals
from app.services.scheduler_diagnostics import unavailable_instrument_message
from app.services.scheduler_data import load_scheduler_data, load_task_children
from app.services.scheduler_helpers import (
    build_compatibility,
    build_dependencies,
    build_maintenance_windows,
    time_horizon,
)
from app.services.approval_gate_service import unapproved_gate_context
from app.services.schedule_advance_notification_service import (
    capture_task_schedule_windows,
)
from app.services.calendar_service import ensure_calendar_range
from app.services.scheduler_solver_trace_service import SolverTrace
from app.services.scheduler_cross_project_setup import (
    add_cross_project_switch_constraints,
    cross_project_setup_units,
)
from app.services.scheduler_failure_response import build_failure_response
from app.services.scheduler_preflight import (
    narrow_compat_to_fixed_instruments,
    validate_schedulable_input,
)
from app.services.scheduler_working_calendar import build_working_calendar
from app.services.scheduler_task_variables import build_task_variables
from app.services.scheduler_soft_constraints import (
    add_milestone_tardiness,
    add_precedence_constraints,
    build_dependency_gap_penalties,
    build_early_start_penalties,
    build_project_instrument_penalties,
    build_stability_penalties,
    sibling_cohesion_weight,
)
from app.services.scheduler_result_service import persist_schedule_result


_logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, db: Session, reuse_prepared_context: bool = False):
        self.db = db
        # 交期建议搜索会为几十上百个候选结题日各调一次 generate()。候选日期
        # 只改 project.end_date，不影响工作日历和固定时间槽，但这两步占了单次
        # 调用的大半时间（实测日历前缀和 0.4 秒、固定时间槽 0.13 秒，求解本身
        # 只有 0.06 秒），逐次重建纯属浪费。开启后在同一个实例内复用。
        # 常规请求每次新建实例、不开缓存，读到的始终是最新数据。
        self._prepared: dict | None = {} if reuse_prepared_context else None

    def _prepare(self, key, build):
        if self._prepared is None:
            return build()
        if key not in self._prepared:
            self._prepared[key] = build()
        return self._prepared[key]

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
        replaceable_after: datetime | None = None,
        planning_start_at: datetime | None = None,
        planning_end_at: datetime | None = None,
        preserved_status_task_ids: set[int] | None = None,
        preserved_slot_ids: set[int] | None = None,
        setup_exempt_task_pairs: set[frozenset[int]] | None = None,
        fixed_instrument_ids: dict[int, int] | None = None,
        allow_unassigned_human_task_ids: set[int] | None = None,
        additional_dependency_gaps: dict[tuple[int, int], int] | None = None,
        released_slot_intervals: dict[int, list[tuple]] | None = None,
        feasibility_only: bool = False,
    ) -> dict:
        if current_project_id is None:
            return {"status": "error", "message": "排程请求缺少当前项目ID"}
        tasks, instruments = load_scheduler_data(
            self.db,
            project_ids,
            task_ids,
            excluded_task_ids,
            replaceable_task_ids,
            occupancy_project_ids={
                current_project_id, *(project_ids or ()),
            },
        )
        # 未签批方案的下游任务必须参与求解，否则它们的工时在签批前不可见，
        # 项目完工时间与延期判断都会系统性偏乐观。签批节点本身仍被排除在
        # 求解器之外（0 耗时），依赖由 _effective_predecessor_ids 桥接到它的
        # 前置任务。求解结果不为这些任务落地时间槽，详见 scheduler_persistence。
        if not tasks:
            return {"status": "ok", "message": "没有可排程任务", "timeslots_created": 0}
        # Resource release is a first-class priority: once hard constraints
        # allow a task to start, avoid unexplained idle gaps before it.
        early_start_task_ids = early_start_task_ids or {task.id for task in tasks}
        if original_schedule_windows is None:
            original_schedule_windows = capture_task_schedule_windows(
                self.db,
                {task.id for task in tasks},
            )
        preflight_error = validate_schedulable_input(
            self.db,
            tasks=tasks,
            instruments=instruments,
            current_project_id=current_project_id,
            allow_unassigned_human_task_ids=allow_unassigned_human_task_ids,
        )
        if preflight_error:
            return preflight_error

        constraints = get_solver_constraints(self.db)
        horizon_start, horizon_end, total_units = time_horizon(
            planning_start_at,
            planning_end_at,
        )
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
        fixed_instrument_error = narrow_compat_to_fixed_instruments(
            compat, fixed_instrument_ids,
        )
        if fixed_instrument_error:
            return fixed_instrument_error
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
        working_calendar = self._prepare(
            (
                "working_calendar", horizon_start, horizon_end, total_units,
                tuple(sorted(instrument.id for instrument in instruments)),
                len(maint_windows),
            ),
            lambda: build_working_calendar(
                self.db,
                instruments=instruments,
                constraints=constraints,
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                total_units=total_units,
                maint_windows=maint_windows,
            ),
        )
        working_context = working_calendar.context
        working_params = working_calendar.params
        calendar_days = working_calendar.calendar_days
        global_prefix_sum = working_calendar.global_prefix_sum
        instrument_prefix_sums = working_calendar.instrument_prefix_sums

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
        fixed_slots = self._prepare(
            (
                "fixed_slots", frozenset(task.id for task in tasks),
                frozenset(relevant_instrument_ids), frozenset(relevant_assignee_ids),
            ),
            lambda: load_fixed_slots(
                self.db,
                {task.id for task in tasks},
                relevant_instrument_ids,
                relevant_assignee_ids,
            ),
        )
        # A preserved running slot remains the execution anchor. It must not
        # be loaded again as a fixed interval while the same task is being
        # re-solved, otherwise a zero-length/frozen anchor can suppress all
        # future presence intervals for that task.
        if preserved_slot_ids:
            fixed_slots = [
                slot for slot in fixed_slots
                if slot.id not in preserved_slot_ids
            ]
        fixed_bridge_reservations = load_fixed_bridge_reservations(
            self.db,
            {task.id for task in tasks},
            relevant_instrument_ids,
        )

        model = cp_model.CpModel()

        variables, variable_error = build_task_variables(
            model,
            tasks=tasks,
            instruments=instruments,
            compat=compat,
            constraints=constraints,
            approval_bounds=approval_bounds,
            horizon_start=horizon_start,
            total_units=total_units,
            global_prefix_sum=global_prefix_sum,
            instrument_prefix_sums=instrument_prefix_sums,
            fixed_slots=fixed_slots,
            remaining_duration_minutes=remaining_duration_minutes,
        )
        if variable_error:
            return variable_error
        capacity_intervals = variables.capacity_intervals
        presences = variables.presences
        inst_starts = variables.inst_starts
        inst_ends = variables.inst_ends
        task_starts = variables.task_starts
        task_ends = variables.task_ends
        task_intervals = variables.task_intervals
        task_tardiness = variables.task_tardiness
        split_unit_presences = variables.split_unit_presences
        CROSS_PROJECT_SETUP_UNITS = cross_project_setup_units(constraints)
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
            maint_windows,
        )
        add_human_capacity_constraints(
            model,
            tasks,
            task_intervals,
            fixed_slots,
            horizon_start,
            total_units,
        )
        switch_penalties = add_cross_project_switch_constraints(
            model,
            tasks=tasks,
            instruments=instruments,
            presences=presences,
            inst_starts=inst_starts,
            inst_ends=inst_ends,
            setup_units=CROSS_PROJECT_SETUP_UNITS,
            setup_exempt_task_pairs=setup_exempt_task_pairs,
        )

        missing_pred_ends = add_precedence_constraints(
            model,
            self.db,
            task_deps=task_deps,
            task_starts=task_starts,
            task_ends=task_ends,
            horizon_start=horizon_start,
            precedence_enabled=constraints["precedence"].is_enabled,
            additional_dependency_gaps=additional_dependency_gaps,
        )
        dependency_gap_penalties = build_dependency_gap_penalties(
            model,
            task_deps=task_deps,
            task_starts=task_starts,
            task_ends=task_ends,
            total_units=total_units,
        )
        early_start_penalties = build_early_start_penalties(
            task_starts, early_start_task_ids,
        )
        project_inst_used_vars = build_project_instrument_penalties(
            model, tasks=tasks, instruments=instruments, presences=presences,
        )
        add_milestone_tardiness(
            model,
            tasks=tasks,
            task_ends=task_ends,
            task_tardiness=task_tardiness,
            horizon_start=horizon_start,
            total_units=total_units,
            milestone_enabled=constraints["milestone_deadline"].is_enabled,
        )
        stability_penalties = build_stability_penalties(
            model,
            stability_task_ids=stability_task_ids,
            original_schedule_windows=original_schedule_windows,
            task_starts=task_starts,
            horizon_start=horizon_start,
            total_units=total_units,
        )

        add_scheduler_objective(
            model,
            tasks,
            task_starts,
            task_ends,
            task_tardiness,
            switch_penalties,
            project_inst_used_vars,
            total_units,
            sibling_cohesion_weight(constraints["sibling_task_cohesion"]),
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
        if feasibility_only:
            # 只问排不排得下，找到任意可行解即可返回，不必继续优化目标函数。
            # 不影响结论：可行就是可行；判定不可行仍然要走完整证明。
            solver.parameters.stop_after_first_solution = True
        # 交期建议一次搜索要探测上百个候选日期，逐次落一份求解日志会在几分钟内
        # 生成上千个文件（实测一天 3267 个），而这些探测只关心可行与否，日志
        # 没有排查价值。真实排程仍然完整留痕。
        solver.parameters.log_search_progress = not feasibility_only
        solver.parameters.log_to_stdout = False
        solver_trace = None if feasibility_only else SolverTrace(
            current_project_id, len(tasks), mode, solver_time_limit,
        )
        if solver_trace:
            solver_trace.write_model(model)
            solver_trace.write_fixed_slot_registry(fixed_slots)
            solver.log_callback = solver_trace.write
        status = solver.Solve(model)
        elapsed_ms = (
            solver_trace.finish(solver, solver.StatusName(status))
            if solver_trace else round(solver.WallTime() * 1000)
        )
        _logger.info(
            "scheduler_solve project_id=%s tasks=%s mode=%s status=%s elapsed_ms=%s trace=%s",
            current_project_id,
            len(tasks),
            mode,
            solver.StatusName(status),
            elapsed_ms,
            solver_trace.path if solver_trace else "-",
        )

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return build_failure_response(
                self.db,
                solver=solver,
                status=status,
                tasks=tasks,
                instruments=instruments,
                compat=compat,
                constraints=constraints,
                task_deps=task_deps,
                missing_pred_ends=missing_pred_ends,
                fixed_slots=fixed_slots,
                global_prefix_sum=global_prefix_sum,
                instrument_prefix_sums=instrument_prefix_sums,
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                total_units=total_units,
                current_project_id=current_project_id,
                relaxed_project_end_task_ids=relaxed_project_end_task_ids,
                include_failure_diagnostics=include_failure_diagnostics,
                released_slot_intervals=released_slot_intervals,
                replan_request={
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

        if feasibility_only:
            # 交期建议只需要知道这个结题日排不排得下。落地时间槽、重建桥接
            # 预留、做重排校验都会被调用方回滚掉，实测占单次探测四成时间。
            return {"status": "ok", "solver_status": solver.StatusName(status)}

        return persist_schedule_result(
            self.db,
            solver=solver,
            status=status,
            tasks=tasks,
            instruments=instruments,
            task_starts=task_starts,
            task_ends=task_ends,
            presences=presences,
            split_unit_presences=split_unit_presences,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            working_context=working_context,
            working_params=working_params,
            calendar_days=calendar_days,
            maint_windows=maint_windows,
            freeze_days=freeze_days,
            forecast_task_ids=forecast_task_ids,
            instrument_bridges=instrument_bridges,
            preserved_status_task_ids=preserved_status_task_ids,
            preserved_slot_ids=preserved_slot_ids,
            replaceable_task_ids=replaceable_task_ids,
            replaceable_after=replaceable_after,
            business_task_deps=business_task_deps,
            queue_task_deps=queue_task_deps,
            original_schedule_windows=original_schedule_windows,
            advance_notification_reason=advance_notification_reason,
            emit_advance_notifications=emit_advance_notifications,
            rollback_on_conflict=rollback_on_conflict,
            commit=commit,
        )

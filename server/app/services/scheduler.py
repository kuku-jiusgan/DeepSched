import inspect
import logging

from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from ortools.sat.python import cp_model
from app.core.config import get_settings
from app.services.schedule_rule_service import get_solver_constraints, solver_constraints_from_snapshot
from app.services.scheduler_fixed_slots import (
    add_human_capacity_constraints,
    add_instrument_capacity_constraints,
    load_fixed_bridge_reservations,
    load_fixed_slots,
    snapshot_fixed_slots,
    snapshot_bridge_reservations,
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
    datetime_to_units,
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
from app.services.schedule_run_lock_service import SCHEDULE_RUN, schedule_run_lock
from app.services.scheduler_failure_response import build_failure_response
from app.services.scheduler_pending_approval import pending_approval_end_bounds
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
from app.services.schedule_snapshot import SimulationContext


_logger = logging.getLogger(__name__)


def _missing_calendar_days(calendar_days: dict, start, end) -> list:
    """快照日历在求解视界内缺哪些天。"""
    from datetime import timedelta

    missing = []
    day = start
    while day <= end:
        if day not in calendar_days:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    return missing


# 交期建议的职责是"把刚才失败的那道题换个结题日再跑一遍"，参数少带一个，解的
# 就不是同一道题。暂停切换曾因此被重建成"四个任务从头完整排一遍"——真实那次是
# "只排剩余工时、接替任务锁在当下、被暂停任务保持暂停"，在被改写的题上验证出来
# 的方案对实际操作并不成立。所以这里不再手挑字段，改为整体快照实参，只排除下面
# 这些必须由验证方自己决定或与可行性无关的。
_REPLAY_EXCLUDED_KWARGS = frozenset({
    "commit",                       # 验证一律不落地
    "emit_advance_notifications",   # 验证不发提前通知
    "include_failure_diagnostics",  # 验证不需要诊断
    "feasibility_only",             # 验证只问可行与否
    "solver_time_limit",            # 验证用更短的求解预算
    "advance_notification_reason",
    "rollback_on_conflict",
    "current_project_id",           # 由作业按候选项目设置
    "released_slot_intervals",      # 只服务于失败诊断
    "original_schedule_windows",    # 只影响目标函数的稳定性惩罚，不影响可行性
    "simulation_context",
})


def replayable_kwargs(scope: dict) -> dict:
    """从 generate 入口的局部作用域里取出可回放的实参。

    必须在任何形参被重新赋值之前调用。
    """
    names = set(inspect.signature(SchedulerService._generate).parameters) - {"self"}
    return {
        name: scope[name]
        for name in sorted(names - _REPLAY_EXCLUDED_KWARGS)
        if scope.get(name) is not None
    }


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

    def generate(self, *args, **kwargs) -> dict:
        """排程入口：全局互斥，同一时刻只允许一次排程计算。

        两次排程都要删改同一批任务和时间槽，撞在一起时原先只能靠数据库行锁被动
        等待，等满 50 秒才报错。这里改成抢不到锁就当场退回，让调用方能明确区分
        "正忙，请稍后重试"和"真的排不下"。锁按线程可重入，后台方案搜索在自己的
        锁里反复调用求解器不受影响。

        模拟求解不持这把锁：它只读、不落库、不发通知，与真实排程没有互斥关系，
        而且方案搜索要并发跑几百次候选，持锁会让它们互相排队，也会把真实排程
        挡在后面。真实排程仍然独占。
        """
        if kwargs.get("simulation_context") is not None:
            return self._generate(*args, **kwargs)
        with schedule_run_lock(SCHEDULE_RUN):
            return self._generate(*args, **kwargs)

    def _generate(
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
        project_end_date_overrides: dict[int, datetime] | None = None,
        simulation_context: SimulationContext | None = None,
    ) -> dict:
        if simulation_context is not None:
            if commit or emit_advance_notifications or not feasibility_only:
                return {"status": "error", "message": "模拟排程禁止持久化或发送通知"}
            project_end_date_overrides = simulation_context.deadline_overrides
            solver_time_limit = simulation_context.solver_time_limit
        replan_request = replayable_kwargs(locals())
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
        # 未签批方案的下游任务不进入求解，改为收窄所在项目的完工上界，
        # 详见 scheduler_pending_approval。
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

        constraints = (
            solver_constraints_from_snapshot(
                simulation_context.snapshot.rule_params,
                simulation_context.snapshot.rule_enabled,
            )
            if simulation_context is not None
            else get_solver_constraints(self.db)
        )
        horizon_start, horizon_end, total_units = time_horizon(
            planning_start_at,
            planning_end_at,
        )
        snapshot_calendar = None
        if simulation_context is None:
            ensure_calendar_range(self.db, horizon_start.date(), horizon_end.date())
        else:
            # 模拟不补日历也不查库：日历就在快照里，缺不缺按快照自己核对。
            # 此前这里查 SysCalendar 计数，既多一次库读，缺日历时报出来的又是
            # "模拟排程所需工作日历不完整"——真实排程会自动补齐，模拟却因为一件
            # 与方案无关的事整批失败，排查时完全指错方向。
            snapshot_calendar = {
                item.day: {
                    "is_working_day": item.is_working_day,
                    "day_type": item.day_type,
                    "holiday_name": item.holiday_name,
                }
                for item in simulation_context.snapshot.calendar_days
            }
            missing = _missing_calendar_days(
                snapshot_calendar, horizon_start.date(), horizon_end.date(),
            )
            if missing:
                return {
                    "status": "error",
                    "message": (
                        f"模拟排程的工作日历快照缺 {len(missing)} 天"
                        f"（{missing[0]} 起），请重新抓取快照"
                    ),
                }
        approval_bounds, forecast_task_ids = unapproved_gate_context(self.db, tasks)
        forecast_tasks = [task for task in tasks if task.id in forecast_task_ids]
        if forecast_tasks:
            tasks = [task for task in tasks if task.id not in forecast_task_ids]
            if not tasks:
                return {
                    "status": "ok",
                    "message": "当前任务都在等待方案签批",
                    "timeslots_created": 0,
                }
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
        if maintenance_rule.is_enabled and simulation_context is not None:
            maint_windows = [
                (item.instrument_id, (
                    max(0, datetime_to_units(item.start_time, horizon_start)),
                    datetime_to_units(item.end_time, horizon_start),
                ))
                for item in simulation_context.snapshot.maintenance_windows
                if datetime_to_units(item.end_time, horizon_start) > 0
            ]
        else:
            maint_windows = (
                build_maintenance_windows(instruments, horizon_start)
                if maintenance_rule.is_enabled else []
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
                calendar_days=snapshot_calendar,
                rule_params=(constraints["working_hours"].params if simulation_context is not None else None),
                rule_enabled=(constraints["working_hours"].is_enabled if simulation_context is not None else None),
            ),
        )
        working_context = working_calendar.context
        working_params = working_calendar.params
        calendar_days = working_calendar.calendar_days
        global_prefix_sum = working_calendar.global_prefix_sum
        instrument_prefix_sums = working_calendar.instrument_prefix_sums

        project_end_bounds = pending_approval_end_bounds(
            forecast_tasks, global_prefix_sum, horizon_start, total_units,
            project_end_date_overrides=project_end_date_overrides,
        )

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
        if simulation_context is not None:
            fixed_slots = load_fixed_slots(
                self.db,
                {task.id for task in tasks},
                relevant_instrument_ids,
                relevant_assignee_ids,
                slot_rows=snapshot_fixed_slots(simulation_context.snapshot.time_slots),
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
        if simulation_context is not None:
            fixed_bridge_reservations = snapshot_bridge_reservations(
                simulation_context.snapshot.bridge_reservations,
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
            project_end_bounds=project_end_bounds,
            project_end_date_overrides=project_end_date_overrides,
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
                replan_request=replan_request,
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

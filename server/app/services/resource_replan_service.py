from __future__ import annotations

import logging
from datetime import datetime

from app.models import Task, TimeSlot
from app.models import ScheduleCalendarSnapshot
from app.services.schedule_replan_closure_service import collect_replan_task_ids
from app.services.scheduler import SchedulerService
from app.services.resource_replan_conflict_service import external_conflict_task_ids


_logger = logging.getLogger(__name__)


def replan_resource_closure(
    db,
    seed_task_ids: set[int],
    released_at: datetime,
    current_project_id: int | None = None,
    *,
    earliest_start_bounds: dict[int, datetime] | None = None,
    advance_notification_reason: str = "资源变更重排",
    commit: bool = False,
    remaining_duration_minutes: dict[int, int] | None = None,
    planning_start_at: datetime | None = None,
    planning_end_at: datetime | None = None,
    replaceable_after: datetime | None = None,
    max_iterations: int = 3,
    expand_closure: bool = True,
    preserved_status_task_ids: set[int] | None = None,
    additional_dependencies: list[tuple[int, int]] | None = None,
    preserved_slot_ids: set[int] | None = None,
    setup_exempt_task_pairs: set[frozenset[int]] | None = None,
    fixed_instrument_ids: dict[int, int] | None = None,
    allow_unassigned_human_task_ids: set[int] | None = None,
    additional_dependency_gaps: dict[tuple[int, int], int] | None = None,
    emit_advance_notifications: bool = True,
    solver_time_limit: float = 30.0,
) -> dict:
    """Run the authoritative CP-SAT replan for a resource-impact closure."""
    if not seed_task_ids:
        return {
            "status": "ok",
            "message": "没有受影响任务",
            "timeslots_created": 0,
            "replan_diagnostic": {"seed_task_ids": [], "iterations": []},
        }
    # 排序固定：兜底的 current_project_id 取 seed_tasks[0]，不排序的话取到哪个
    # 项目取决于数据库返回顺序，同一批任务两次调用可能落到不同项目。
    seed_tasks = db.query(Task).filter(Task.id.in_(seed_task_ids)).order_by(Task.id).all()
    if not seed_tasks:
        raise ValueError("资源重排没有找到种子任务")
    closure_ids = set(seed_task_ids)
    if expand_closure:
        rows = [(task.assignee_id,) for task in seed_tasks]
        assignee_ids = {value for (value,) in rows if value is not None}
        instrument_rows = db.query(TimeSlot.instrument_id).filter(
            TimeSlot.task_id.in_(seed_task_ids), TimeSlot.instrument_id.isnot(None),
        ).distinct().all()
        closure_ids = collect_replan_task_ids(
            db,
            closure_ids,
            {value for (value,) in instrument_rows},
            assignee_ids,
            released_at,
        ) or closure_ids
    closure_projects = {
        project_id for (project_id,) in db.query(Task.project_id).filter(
            Task.id.in_(closure_ids),
        ).distinct().all()
    }
    if not closure_projects:
        raise ValueError("资源重排任务没有关联项目")
    current_project_id = current_project_id or seed_tasks[0].project_id
    diagnostic = {
        "seed_task_ids": sorted(seed_task_ids),
        "released_at": released_at.isoformat(),
        "current_project_id": current_project_id,
        "expand_closure": expand_closure,
        "max_iterations": max(1, max_iterations),
        "initial_closure_task_ids": sorted(closure_ids),
        "iterations": [],
    }
    last_result = None
    savepoint = db.begin_nested()
    try:
        for iteration in range(1, max(1, max_iterations) + 1):
            iteration_diagnostic = {
                "iteration": iteration,
                "closure_task_ids": sorted(closure_ids),
            }
            diagnostic["iterations"].append(iteration_diagnostic)
            last_result = SchedulerService(db).generate(
                project_ids=sorted(closure_projects),
                task_ids=sorted(closure_ids),
                current_project_id=current_project_id,
                earliest_start_bounds=earliest_start_bounds,
                advance_notification_reason=advance_notification_reason,
                # The outer service owns the transaction so the savepoint can
                # roll back every partial iteration, even for commit=True.
                commit=False,
                remaining_duration_minutes=remaining_duration_minutes,
                replaceable_task_ids=closure_ids,
                planning_start_at=planning_start_at,
                planning_end_at=planning_end_at,
                replaceable_after=replaceable_after,
                preserved_status_task_ids=preserved_status_task_ids,
                additional_dependencies=additional_dependencies,
                preserved_slot_ids=preserved_slot_ids,
                setup_exempt_task_pairs=setup_exempt_task_pairs,
                fixed_instrument_ids=fixed_instrument_ids,
                allow_unassigned_human_task_ids=allow_unassigned_human_task_ids,
                additional_dependency_gaps=additional_dependency_gaps,
                emit_advance_notifications=emit_advance_notifications,
                solver_time_limit=solver_time_limit,
                rollback_on_conflict=False,
            )
            iteration_diagnostic.update(_solver_result_diagnostic(last_result))
            if last_result.get("status") != "ok":
                savepoint.rollback()
                return _finish_replan_result(db, last_result, diagnostic)
            external_ids = external_conflict_task_ids(
                db, last_result["schedule_run_id"], closure_ids,
            )
            iteration_diagnostic["external_conflict_task_ids"] = sorted(external_ids)
            last_result["external_conflict_task_ids"] = sorted(external_ids)
            if not external_ids:
                savepoint.commit()
                if commit:
                    db.commit()
                return _finish_replan_result(db, last_result, diagnostic)
            if not expand_closure:
                savepoint.rollback()
                return _finish_replan_result(db, {
                    "status": "error",
                    "message": "受限重排窗口与窗口外任务发生资源冲突",
                    "external_conflict_task_ids": sorted(external_ids),
                }, diagnostic)
            closure_ids.update(external_ids)
            closure_projects.update(
                project_id for (project_id,) in db.query(Task.project_id).filter(
                    Task.id.in_(external_ids),
                ).all()
            )
    except Exception:
        savepoint.rollback()
        raise
    savepoint.rollback()
    last_result["status"] = "error"
    last_result["message"] = "资源重排在限定次数内未消除外部冲突"
    return _finish_replan_result(db, last_result, diagnostic)


def _solver_result_diagnostic(result: dict) -> dict:
    """Keep only stable solver facts that help reproduce a replan decision."""
    keys = ("status", "message", "schedule_run_id", "solver_status", "objective_value")
    return {key: result[key] for key in keys if key in result}


def _finish_replan_result(db, result: dict, diagnostic: dict) -> dict:
    diagnostic["final_closure_task_ids"] = diagnostic["iterations"][-1]["closure_task_ids"]
    result["replan_diagnostic"] = diagnostic
    schedule_run_id = result.get("schedule_run_id")
    if schedule_run_id:
        snapshot = db.query(ScheduleCalendarSnapshot).filter(
            ScheduleCalendarSnapshot.schedule_run_id == schedule_run_id,
        ).first()
        if snapshot is not None:
            snapshot.replan_diagnostic = diagnostic
            db.flush()
    _logger.info(
        "resource_replan status=%s seed_task_ids=%s final_closure_task_ids=%s iterations=%s",
        result.get("status"),
        diagnostic["seed_task_ids"],
        diagnostic["final_closure_task_ids"],
        len(diagnostic["iterations"]),
    )
    return result

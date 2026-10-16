from datetime import datetime, timedelta

from app.domain.task_status import resolve_task_execution_status
from app.schemas.workspace_schemas import WorkspaceNightRunOut
from app.services.instrument_working_time_service import load_working_time_context


def night_run_action(db, task, segments, now: datetime, context=None) -> WorkspaceNightRunOut:
    if resolve_task_execution_status(task) != "running":
        return WorkspaceNightRunOut(reason="只有进行中的任务才能设置夜间运行")
    candidates = [
        slot for slot in segments
        if not slot.is_night_run and slot.instrument_id
        and slot.lifecycle_status == "active"
        and slot.status in {"running", "scheduled"}
        and slot.plan_start <= now and slot.plan_end.date() == now.date()
    ]
    if not candidates:
        return WorkspaceNightRunOut(reason="当前没有可登记夜间运行的今日计划时段")
    source = max(candidates, key=lambda slot: (slot.plan_end, slot.id))
    if context is None:
        context = load_working_time_context(db, source.plan_start, source.plan_end + timedelta(days=1))
    day_end = context.policy_for(source.instrument_id).day_end_minutes
    work_end = source.plan_end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=day_end)
    reason = ""
    if source.plan_end < work_end:
        reason = f"当天计划结束时间需不早于仪器有效工作时段结束时间 {day_end // 60:02d}:{day_end % 60:02d}"
    return WorkspaceNightRunOut(
        slot_id=source.id, start_at=source.plan_end,
        is_eligible=not reason, reason=reason,
    )

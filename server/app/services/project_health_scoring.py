from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.services.task_progress_service import planned_task_minutes


WEIGHTS = {
    "delivery": 40,
    "progress": 25,
    "critical_path": 20,
    "blockers": 10,
    "schedule": 5,
}
COMPLETED_STATUSES = {"done", "completed"}
BLOCKER_STATUSES = {"blocked", "paused", "interrupted", "waiting_external"}


@dataclass(frozen=True)
class HealthFactorResult:
    key: str
    label: str
    score: int
    max_score: int
    status: str
    detail: str


@dataclass(frozen=True)
class HealthScoreResult:
    score: int
    level: str
    factors: list[HealthFactorResult]
    reasons: list[str]


def calculate_health_score(
    tasks: list,
    active_slots_by_task: dict[int, list],
    delivery_status: str,
    predicted_end: datetime | None,
    due_date: datetime | None,
    now: datetime,
    critical_task_ids: set[int],
    remaining_hours: dict[int, float],
    schedule_state: str,
) -> HealthScoreResult:
    open_tasks = [task for task in tasks if _is_open_task(task)]
    total_hours = sum(max(0.0, value) for value in remaining_hours.values())
    completed_hours = sum(
        max(0.0, planned_task_minutes(task) / 60 - remaining_hours.get(task.id, 0))
        for task in tasks
    )
    planned_hours = completed_hours + total_hours
    if planned_hours > 0:
        progress_ratio = completed_hours / planned_hours
        progress_detail = f"已完成 {completed_hours:.1f} / {planned_hours:.1f} 小时"
    else:
        completed_count = sum(task.status in COMPLETED_STATUSES for task in tasks)
        progress_ratio = completed_count / len(tasks) if tasks else 0
        progress_detail = f"已完成 {completed_count} / {len(tasks)} 项任务"

    delivery_score = {"on_time": 40, "at_risk": 20, "overdue": 0}.get(delivery_status, 0)
    progress_score = round(progress_ratio * WEIGHTS["progress"])

    critical_hours = sum(remaining_hours.get(task_id, 0) for task_id in critical_task_ids)
    critical_ratio = critical_hours / total_hours if total_hours else 0
    critical_risk = min(
        1.0,
        critical_ratio
        + _critical_date_risk(active_slots_by_task, critical_task_ids, due_date)
        + (0.25 if any(
            task.id in critical_task_ids
            and (task.status in BLOCKER_STATUSES or getattr(task, "delay_status", "") == "delayed")
            for task in open_tasks
        ) else 0),
    )
    critical_score = round((1 - critical_risk) * WEIGHTS["critical_path"])

    blocked_hours = sum(
        remaining_hours.get(task.id, 0)
        for task in open_tasks
        if task.status in BLOCKER_STATUSES
    )
    blocker_ratio = blocked_hours / total_hours if total_hours else 0
    blocker_score = round((1 - min(1.0, blocker_ratio)) * WEIGHTS["blockers"])

    unscheduled = [
        task for task in open_tasks
        if not active_slots_by_task.get(task.id)
        and not (task.is_external_gate and task.gate_status == "approved")
    ]
    completeness_ratio = 1 - (len(unscheduled) / len(open_tasks) if open_tasks else 0)
    if schedule_state in {"dirty", "not_scheduled"} and open_tasks:
        completeness_ratio *= 0.5
    schedule_score = round(max(0, completeness_ratio) * WEIGHTS["schedule"])

    factors = [
        HealthFactorResult("delivery", "交付预测", delivery_score, 40, _delivery_factor_status(delivery_status), _delivery_detail(delivery_status, predicted_end, due_date)),
        HealthFactorResult("progress", "进度完成度", progress_score, 25, _factor_status(progress_score, 25), progress_detail),
        HealthFactorResult("critical_path", "关键路径风险", critical_score, 20, _factor_status(critical_score, 20), f"关键路径剩余工时 {critical_hours:.1f} 小时"),
        HealthFactorResult("blockers", "阻塞/暂停/等待外部", blocker_score, 10, _factor_status(blocker_score, 10), f"阻塞类任务剩余工时 {blocked_hours:.1f} 小时"),
        HealthFactorResult("schedule", "排程完整性", schedule_score, 5, _factor_status(schedule_score, 5), f"未排程任务 {len(unscheduled)} 项"),
    ]
    score = max(0, min(100, sum(item.score for item in factors)))
    reasons = _risk_reasons(open_tasks, unscheduled, delivery_status, predicted_end, due_date, now)
    if not reasons and score < 80:
        reasons.append("综合评分低于绿色阈值")
    if not open_tasks:
        reasons = []
    level = _health_level(score, open_tasks, delivery_status, predicted_end, due_date, now)
    return HealthScoreResult(score, level, factors, reasons)


def _critical_date_risk(slots_by_task: dict[int, list], task_ids: set[int], due_date: datetime | None) -> float:
    if not due_date:
        return 0.0
    latest = max(
        (slot.plan_end for task_id in task_ids for slot in slots_by_task.get(task_id, [])),
        default=None,
    )
    return 0.5 if latest and latest > due_date else 0.0


def _delivery_detail(status: str, predicted_end: datetime | None, due_date: datetime | None) -> str:
    if not predicted_end or not due_date:
        return "缺少结题日期或有效完工预测"
    return f"预计 {predicted_end:%Y-%m-%d}，结题日 {due_date:%Y-%m-%d}"


def _factor_status(score: int, maximum: int) -> str:
    ratio = score / maximum if maximum else 0
    return "good" if ratio >= 0.8 else "warning" if ratio >= 0.5 else "risk"


def _delivery_factor_status(status: str) -> str:
    return {"on_time": "good", "at_risk": "warning", "overdue": "risk"}.get(status, "risk")


def _risk_reasons(open_tasks, unscheduled, delivery_status, predicted_end, due_date, now) -> list[str]:
    reasons: list[str] = []
    if delivery_status == "overdue":
        reasons.append("预测完工日晚于结题日期")
    elif due_date and now.date() == due_date.date() and open_tasks:
        reasons.append("结题日仍有未完成任务")
    elif due_date and (due_date.date() - now.date()).days <= 3 and open_tasks:
        reasons.append("距离结题日不超过 3 天且仍有未完成任务")
    if predicted_end and due_date and predicted_end > due_date and open_tasks and "预测完工日晚于结题日期" not in reasons:
        reasons.append("预测完工日晚于结题日期")
    for task in open_tasks:
        if task.status == "waiting_external":
            reasons.append(f"等待客户/外部签批：{task.name}")
        elif task.status in {"blocked", "paused", "interrupted"}:
            reasons.append(f"任务{_status_label(task.status)}：{task.name}")
    if unscheduled:
        reasons.append(f"有 {len(unscheduled)} 项未完成任务没有有效排程")
    return list(dict.fromkeys(reasons))


def _status_label(status: str) -> str:
    return {"blocked": "阻塞", "paused": "暂停", "interrupted": "中断"}.get(status, status)


def _health_level(score: int, open_tasks, delivery_status, predicted_end, due_date, now) -> str:
    if not open_tasks:
        return "green" if score >= 80 else "yellow" if score >= 50 else "red"
    if delivery_status == "overdue" or (predicted_end and due_date and predicted_end > due_date):
        return "red"
    if due_date and (now.date() >= due_date.date() or (due_date.date() - now.date()).days <= 3):
        return "yellow" if score >= 50 else "red"
    return "green" if score >= 80 else "yellow" if score >= 50 else "red"


def _is_open_task(task) -> bool:
    return task.status not in COMPLETED_STATUSES and not (
        task.is_external_gate and task.gate_status == "approved"
    )

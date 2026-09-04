from __future__ import annotations

from typing import Any
from types import SimpleNamespace

from app.models import ScheduleRule
from app.services.scheduler_helpers import (
    DEFAULT_WORK_END_MINUTES,
    DEFAULT_WORK_START_MINUTES,
    parse_working_time,
)


CONSTRAINT_RULES = [
    {
        "category": "constraint",
        "name": "能力匹配",
        "code": "capability_matching",
        "description": "任务指定的仪器或能力标签必须与候选仪器匹配",
        "params": {"strict": True},
        "sort_order": 10,
    },
    {
        "category": "constraint",
        "name": "维护窗口避让",
        "code": "maintenance_avoidance",
        "description": "任务的有效作业时间必须避开仪器计划维护时段",
        "params": {"strict": True},
        "sort_order": 11,
    },
    {
        "category": "constraint",
        "name": "项目时间窗",
        "code": "project_window",
        "description": "任务必须安排在所属项目的开始与结束时间范围内",
        "params": {"strict": True},
        "sort_order": 12,
    },
    {
        "category": "constraint",
        "name": "冻结期限制",
        "code": "freezing",
        "description": "非紧急任务不得进入冻结期，已冻结任务保持原排程不变",
        "params": {"freeze_days": 3, "strict": True},
        "sort_order": 13,
    },
    {
        "category": "constraint",
        "name": "人员及非仪器任务有效工作时段",
        "code": "working_hours",
        "description": "人员及非仪器任务使用该时段；仪器任务使用仪器管理中的独立时段",
        "params": {
            "day_start": "08:30",
            "day_end": "20:00",
            "include_weekends": False,
            "include_holidays": False,
            "strict": True,
        },
        "sort_order": 14,
    },
    {
        "category": "constraint",
        "name": "仪器唯一分配",
        "code": "instrument_assignment",
        "description": "每个需要仪器的任务必须分配到恰好一台兼容仪器",
        "params": {"strict": True, "locked": True},
        "sort_order": 15,
    },
    {
        "category": "constraint",
        "name": "仪器不重叠",
        "code": "non_overlap",
        "description": "同一台仪器上的任务及已冻结时段不能相互重叠",
        "params": {"strict": True},
        "sort_order": 16,
    },
    {
        "category": "constraint",
        "name": "跨项目切换间隔",
        "code": "cross_project_setup",
        "description": "同一仪器切换到不同项目时必须预留准备时间",
        "params": {"setup_hours": 0.5, "strict": True},
        "sort_order": 17,
    },
    {
        "category": "constraint",
        "name": "前置依赖",
        "code": "precedence",
        "description": "任务必须在全部前置任务完成后才能开始",
        "params": {"strict": True},
        "sort_order": 18,
    },
    {
        "category": "constraint",
        "name": "里程碑期限",
        "code": "milestone_deadline",
        "description": "记录任务超过里程碑期限的延迟量，并由目标函数进行惩罚",
        "params": {"strict": False},
        "sort_order": 19,
    },
    {
        "category": "constraint",
        "name": "同级子任务靠拢",
        "code": "sibling_task_cohesion",
        "description": "同一父任务的同级子任务尽量提前完成，减少后续任务等待最晚子任务的时间",
        "params": {"weight": 1.0, "strict": False},
        "sort_order": 20,
    },
]

DEFAULT_RULES = [
    {
        "category": "decision_variable",
        "name": "时间粒度",
        "code": "time_granularity",
        "description": "排程最小时间单位，单位为分钟",
        "params": {"value": 30, "unit": "分钟", "options": [15, 30, 60]},
        "sort_order": 1,
    },
    {
        "category": "decision_variable",
        "name": "规划窗口",
        "code": "planning_horizon",
        "description": "从当前时刻起向前规划的时长",
        "params": {"value": 90, "unit": "天", "options": [30, 60, 90, 120]},
        "sort_order": 2,
    },
    *CONSTRAINT_RULES,
    {
        "category": "objective",
        "name": "最小化加权延迟",
        "code": "weighted_tardiness",
        "description": "主目标：优先保证高优先级任务按时完成",
        "params": {"weight": 1.0, "enabled": True},
        "sort_order": 30,
    },
    {
        "category": "objective",
        "name": "最小化最大完工时间",
        "code": "makespan",
        "description": "次目标：压缩整体排程周期，提高仪器周转效率",
        "params": {"weight": 0.3, "enabled": True},
        "sort_order": 31,
    },
    {
        "category": "objective",
        "name": "最小化切换代价",
        "code": "switchover_cost_obj",
        "description": "次目标：减少同一仪器上的跨项目切换次数",
        "params": {"weight": 0.2, "enabled": True},
        "sort_order": 32,
    },
]

SUPPORTED_CONSTRAINT_CODES = {rule["code"] for rule in CONSTRAINT_RULES}


class ScheduleRuleNotFoundError(Exception):
    pass


class ScheduleRuleLockedError(Exception):
    pass


def sync_rules(db: Any, commit: bool = True) -> None:
    rules_by_code = {
        rule.code: rule for rule in db.query(ScheduleRule).all()
    }
    unsupported = [
        rule
        for rule in rules_by_code.values()
        if rule.category == "constraint"
        and rule.code not in SUPPORTED_CONSTRAINT_CODES
    ]
    for rule in unsupported:
        db.delete(rule)

    for definition in DEFAULT_RULES:
        rule = rules_by_code.get(definition["code"])
        if rule is None:
            db.add(ScheduleRule(**definition))
            continue

        rule.category = definition["category"]
        rule.name = definition["name"]
        rule.description = definition["description"]
        rule.sort_order = definition["sort_order"]
        default_params = definition["params"]
        current_params = rule.params or {}
        rule.params = {
            key: (
                default_value
                if key == "locked"
                else current_params.get(key, default_value)
            )
            for key, default_value in default_params.items()
        }
        if rule.code == "working_hours":
            rule.params = _normalize_working_hours_params(rule.params)
        if rule.params.get("locked"):
            rule.is_enabled = True

    if commit:
        db.commit()
    else:
        db.flush()


def list_rules(db: Any) -> list[ScheduleRule]:
    sync_rules(db)
    return db.query(ScheduleRule).order_by(ScheduleRule.sort_order).all()


def update_rule(db: Any, rule_id: int, data: Any) -> ScheduleRule:
    rule = _get_rule(db, rule_id)
    if data.params is not None:
        rule.params = (
            _normalize_working_hours_params(data.params)
            if rule.code == "working_hours"
            else data.params
        )
    if data.is_enabled is not None:
        _ensure_toggle_allowed(rule)
        rule.is_enabled = data.is_enabled
    if data.sort_order is not None:
        rule.sort_order = data.sort_order
    db.commit()
    db.refresh(rule)
    return rule


def toggle_rule(db: Any, rule_id: int) -> ScheduleRule:
    rule = _get_rule(db, rule_id)
    _ensure_toggle_allowed(rule)
    rule.is_enabled = not rule.is_enabled
    db.commit()
    db.refresh(rule)
    return rule


def get_solver_constraints(db: Any) -> dict[str, ScheduleRule]:
    sync_rules(db, commit=False)
    return {
        rule.code: rule
        for rule in db.query(ScheduleRule).filter(
            ScheduleRule.category == "constraint"
        ).all()
    }


def solver_constraints_from_snapshot(
    rule_params: dict[str, dict], rule_enabled: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build the minimal rule interface required by the in-memory solver."""
    return {
        code: SimpleNamespace(
            code=code, is_enabled=(rule_enabled or {}).get(code, True),
            params=dict(params or {}),
        )
        for code, params in rule_params.items()
    }


def _get_rule(db: Any, rule_id: int) -> ScheduleRule:
    rule = db.query(ScheduleRule).filter(ScheduleRule.id == rule_id).first()
    if rule is None:
        raise ScheduleRuleNotFoundError
    return rule


def _ensure_toggle_allowed(rule: ScheduleRule) -> None:
    if (rule.params or {}).get("locked"):
        raise ScheduleRuleLockedError


def _normalize_working_hours_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    start_value = normalized.get("day_start")
    end_value = normalized.get("day_end")
    start_minutes = (
        DEFAULT_WORK_START_MINUTES
        if start_value == 8
        else parse_working_time(start_value, DEFAULT_WORK_START_MINUTES)
    )
    end_minutes = parse_working_time(end_value, DEFAULT_WORK_END_MINUTES)
    normalized["day_start"] = _format_minutes(start_minutes)
    normalized["day_end"] = _format_minutes(end_minutes)
    normalized["include_weekends"] = bool(normalized.get("include_weekends", False))
    normalized["include_holidays"] = bool(normalized.get("include_holidays", False))
    return normalized


def _format_minutes(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"

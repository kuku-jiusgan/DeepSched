import logging

from app.domain.errors import DomainNotFoundError
from app.models import AlertRule


_logger = logging.getLogger(__name__)

DEFAULT_ALERT_RULES = [
    {
        "name": "任务开始前提醒",
        "rule_type": "task_start_delay",
        "enabled": True,
        "notify_roles": '["任务负责人"]',
        "threshold_minutes": 15,
        "threshold_percent": 0,
    },
    {
        "name": "任务结束延期",
        "rule_type": "task_end_delay",
        "enabled": True,
        "notify_roles": '["项目负责人","技术员"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
    {
        "name": "排程变更通知",
        "rule_type": "schedule_changed",
        "enabled": True,
        "notify_roles": '["项目负责人"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
    {
        "name": "任务提前前移通知",
        "rule_type": "task_schedule_advanced",
        "enabled": True,
        "notify_roles": '["任务负责人"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
    {
        "name": "任务被动后移通知",
        "rule_type": "task_schedule_delayed",
        "enabled": True,
        "notify_roles": '["任务负责人"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
    {
        "name": "实际工时超标",
        "rule_type": "hours_exceeded",
        "enabled": True,
        "notify_roles": '["项目负责人"]',
        "threshold_minutes": 0,
        "threshold_percent": 120,
    },
    {
        "name": "仪器故障后移",
        "rule_type": "instrument_fault_reschedule",
        "enabled": True,
        "notify_roles": '["项目负责人","技术员"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
    {
        "name": "故障排程冲突",
        "rule_type": "instrument_fault_schedule_conflict",
        "enabled": True,
        "notify_roles": '["项目负责人"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
    {
        "name": "方案待提交客户",
        "rule_type": "approval_pending",
        "enabled": True,
        "notify_roles": '["项目负责人","项目管理员","分析所所长","系统管理员"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
    {
        "name": "方案签批临近或超期",
        "rule_type": "approval_due",
        "enabled": True,
        "notify_roles": '["项目负责人","项目管理员","分析所所长","系统管理员"]',
        "threshold_minutes": 2880,
        "threshold_percent": 0,
    },
    {
        "name": "签批后排程结果",
        "rule_type": "approval_schedule_result",
        "enabled": True,
        "notify_roles": '["项目负责人","项目管理员","分析所所长","系统管理员"]',
        "threshold_minutes": 0,
        "threshold_percent": 0,
    },
]


def list_alert_rules(db) -> list[AlertRule]:
    _ensure_default_rules(db)
    return db.query(AlertRule).order_by(AlertRule.id).all()


def update_alert_rule(db, rule_id: int, data: dict) -> AlertRule:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise DomainNotFoundError("规则不存在")
    for field, value in data.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    _logger.info("通知规则已保存: rule_id=%s fields=%s", rule_id, sorted(data))
    return rule


def _ensure_default_rules(db) -> None:
    existing_types = {row[0] for row in db.query(AlertRule.rule_type).all()}
    missing = [data for data in DEFAULT_ALERT_RULES if data["rule_type"] not in existing_types]
    if not missing:
        return
    db.add_all([AlertRule(**data) for data in missing])
    db.commit()
    _logger.info("补充缺失通知规则: count=%s", len(missing))

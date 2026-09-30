"""统一业务术语目录。原始代码值保留，界面和导出只使用中文展示值。"""
from __future__ import annotations

import re
from typing import Any


def _field(label: str, field_type: str = "text", *, visibility: str = "business", values: dict | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"label": label, "type": field_type, "visibility": visibility}
    if values:
        item["values"] = values
    return item


_FIELD_CATALOG: dict[str, dict[str, Any]] = {
    "actual_start": _field("实际开始时间", "datetime"),
    "affected_project_count": _field("受影响项目数", "number"),
    "affected_task_count": _field("受影响任务数", "number"),
    "affected_task_details": _field("受影响任务详情"),
    "affected_tasks": _field("受影响任务数", "number"),
    "after": _field("调整后"), "after_plan_start": _field("调整后计划开始时间", "datetime"),
    "after_status": _field("调整后状态", "status"), "approved_at": _field("签批时间", "datetime"),
    "assignee": _field("负责人"), "assignee_name": _field("负责人"),
    "before": _field("调整前"), "before_plan_start": _field("调整前计划开始时间", "datetime"),
    "before_status": _field("调整前状态", "status"), "can_shift": _field("可以顺延", "boolean"),
    "client_name": _field("客户名称"), "code": _field("编码"),
    "context": _field("相关信息"), "created": _field("新增任务数", "number"),
    "created_days": _field("补齐日期数", "number"), "date": _field("日期", "date"),
    "day_type": _field("日期类型", values={"workday": "工作日", "weekend": "周末", "holiday": "节假日", "compensate": "调休工作日"}),
    "delay_hours": _field("延期时长（小时）", "number"),
    "delay_started_at": _field("延期开始时间", "datetime"),
    "direct_confirmation": _field("直接确认", "boolean"), "display_name": _field("姓名"),
    "email": _field("邮箱"), "end_date": _field("项目结束时间", "datetime"),
    "estimated_hours": _field("预计工时", "number"),
    "est_duration_hours": _field("预计工时", "number"),
    "expected_approval_at": _field("预计签批时间", "datetime"),
    "holiday_name": _field("节假日名称"), "impact": _field("排程影响"),
    "insert_summary": _field("插单说明"), "instrument_code": _field("仪器编号"),
    "instrument_names": _field("仪器"), "instruments": _field("仪器"),
    "is_active": _field("账号状态", "boolean"), "is_working_day": _field("是否工作日", "boolean"),
    "login_method": _field("登录方式"), "logout_method": _field("退出方式"),
    "manager_name": _field("项目负责人"),
    "mode": _field("排程模式", values={"normal": "常规排程", "insert": "插单排程", "priority": "按优先级插单", "custom_after_task": "指定任务后插入"}),
    "moved_slots": _field("调整的时间段", "slot_changes"),
    "moved_tasks": _field("移动任务数", "number"), "name": _field("名称"),
    "needs_reschedule": _field("需要重新排程", "boolean"),
    "notified_users": _field("已通知人数", "number"),
    "original_end": _field("原计划结束时间", "datetime"),
    "original_start": _field("原计划开始时间", "datetime"),
    "parent": _field("所属任务"), "parent_name": _field("所属任务"),
    "phone": _field("手机号"), "predecessor_names": _field("前置任务"),
    "predecessors": _field("前置任务"), "priority": _field("优先级", "number"),
    "previous_task_id": _field("前序任务", "entity"),
    "previous_last_slot_id": _field("前序任务末时间段", "entity"),
    "previous_slot_id": _field("前一时间段", "entity"),
    "project_code": _field("项目编号"), "project_fields": _field("项目详情"),
    "project_name": _field("项目名称"), "reason": _field("原因"),
    "repair_reason": _field("维修原因"), "requires_human": _field("需要人工", "boolean"),
    "requires_instrument": _field("需要仪器", "boolean"),
    "result": _field("执行结果", "status"), "risk_tasks": _field("风险任务数", "number"),
    "roles": _field("角色"), "shifted_end": _field("顺延后结束时间", "datetime"),
    "shifted_slots": _field("受影响排程数", "number"),
    "shifted_start": _field("顺延后开始时间", "datetime"),
    "snapshot": _field("任务快照"), "source": _field("数据来源", values={"default": "系统默认", "sync": "自动同步", "manual": "手工维护"}),
    "start_date": _field("项目开始时间", "datetime"), "status": _field("状态", "status"),
    "success": _field("执行结果", "boolean"), "switchover_hours": _field("切换时间（小时）", "number"),
    "task_count": _field("关联任务数", "number"), "task_details": _field("任务详情"),
    "task_name": _field("任务名称"), "task_names": _field("任务"), "task_type": _field("任务类型"),
    "updated_days": _field("更新日期数", "number"), "username": _field("登录账号"),
    "wecom_id": _field("企业微信号"), "wecom_notification_sent": _field("企业微信通知已发送", "boolean"),
    "year": _field("年份", "number"),
    "allow_split": _field("允许拆分", "boolean"), "allow_transfer": _field("允许转移", "boolean"),
    "message": _field("处理说明"), "timeslots_created": _field("新增排程数", "number"),
}

# 同一个叶子字段在不同业务对象中含义不同，完整路径优先于通用字段。
_PATH_FIELDS: dict[str, dict[str, Any]] = {
    "project.code": _field("项目编号"), "project.name": _field("项目名称"),
    "project.estimated_hours": _field("预计总工时", "number"),
    "project_fields.code": _field("项目编号"), "project_fields.name": _field("项目名称"),
    "project_fields.estimated_hours": _field("预计总工时", "number"),
    "task_details[].name": _field("任务名称"), "task_details[].estimated_hours": _field("预计工时", "number"),
    "snapshot.name": _field("任务名称"),
}

_HIDDEN_FIELDS = {
    "anchor_task_id", "approval_restriction_id", "client_ids", "group_task_id",
    "instrument_id", "parent_id", "predecessor_task_id", "project_id",
    "removed_execution_segment_ids", "rescheduled_task_ids", "restored_running_task_id",
    "reverted_task_id", "schedule_run_id", "slot_id", "slot_ids", "source_slot_id",
    "source_task_id", "target_slot_id", "target_task_id", "task_id", "task_ids",
    "unlock_task_ids",
}

_STATUS_VALUES = {
    "success": "成功", "failed": "失败", "ok": "成功", "error": "失败",
    "pending": "待处理", "scheduled": "待执行", "running": "进行中",
    "paused": "已暂停", "blocked": "已阻塞", "completed": "已完成",
    "done": "已完成", "cancelled": "已取消", "resolved": "已解决",
}

_TASK_TYPE_VALUES = {
    "group": "任务组", "approval_gate": "方案签批",
    "FFKF_001": "方法开发", "QCFA_001": "方案撰写",
    "FFYZ_001": "方法验证", "SJCL_001": "数据处理", "ZXBG_001": "报告撰写",
}

CATALOG: dict[str, dict[str, dict[str, Any]]] = {
    "action": {
        "schedule_queue_compacted": {"label": "压紧排程队列", "category": "schedule"},
        "historical_timeslot_repaired": {"label": "修复历史时间段", "category": "schedule"},
        "schedule_generated": {"label": "生成排程", "category": "schedule"},
        "schedule_rescheduled": {"label": "重新排程", "category": "schedule"},
    },
    "field": _FIELD_CATALOG,
    "target": {
        "task": {"label": "任务"}, "project": {"label": "项目"},
        "instrument": {"label": "仪器"}, "time_slot": {"label": "任务排程时间段"},
    },
}


def label(domain: str, value: Any, fallback: str | None = None) -> str:
    item = CATALOG.get(domain, {}).get(str(value))
    if item:
        return str(item.get("label"))
    if domain == "action":
        return _fallback_action(str(value))
    if domain == "field":
        return fallback if fallback and re.search(r"[一-鿿]", fallback) else "其他字段"
    return fallback or "其他信息"


def field_meta(key: str, path: str = "") -> dict[str, Any]:
    """按完整业务路径查字段定义；未知实现键使用安全标签。"""
    normalized_path = re.sub(r"\[\d+\]", "[]", path)
    if normalized_path in _PATH_FIELDS:
        return _PATH_FIELDS[normalized_path]
    item = _FIELD_CATALOG.get(key)
    if item:
        return item
    if key in _HIDDEN_FIELDS or key == "id" or key.endswith("_ids") or key.endswith("_id"):
        return {"label": "", "type": "technical", "visibility": "hidden"}
    return {"label": "其他字段", "type": "text", "visibility": "business"}


def _fallback_action(value: str) -> str:
    prefixes = {"create": "新增", "created": "新增", "update": "修改", "updated": "修改", "delete": "删除", "deleted": "删除", "start": "开始", "started": "开始", "pause": "暂停", "paused": "暂停", "complete": "完成", "completed": "完成", "interrupt": "中断", "repair": "修复", "repaired": "修复", "generate": "生成", "generated": "生成", "reschedule": "重新排程", "rescheduled": "重新排程"}
    words = value.lower().split("_")
    verb = next((prefixes[word] for word in words if word in prefixes), "系统操作")
    noun = "任务" if any(word in words for word in ("task", "timeslot", "slot")) else "排程" if "schedule" in words else "系统数据"
    return f"{verb}{noun}" if verb != "系统操作" else "系统操作"


def format_value(key: str, value: Any, path: str = "") -> Any:
    """按目录格式化标量值；对象和数组由展示层递归处理。"""
    meta = field_meta(key, path)
    if value is None:
        return value
    values = meta.get("values") or {}
    if value in values:
        return values[value]
    if meta.get("type") == "status" and isinstance(value, str):
        return _STATUS_VALUES.get(value, value)
    if key == "task_type" and isinstance(value, str):
        return _TASK_TYPE_VALUES.get(value, value)
    if meta.get("type") == "boolean" and isinstance(value, bool):
        return "是" if value else "否"
    if meta.get("type") in {"datetime", "date"} and value:
        return _minute_datetime(value)
    if isinstance(value, str) and len(value) >= 16 and value[4] == "-" and value[7] == "-" and ("T" in value or " " in value):
        return _minute_datetime(value)
    return value


def _minute_datetime(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value[:16].replace("T", " ") if len(value) >= 16 else value


def catalog_payload() -> dict[str, dict[str, dict[str, Any]]]:
    return CATALOG

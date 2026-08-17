"""统一业务术语目录。代码值永远保留，界面和导出只使用 label/format。"""
from __future__ import annotations

from typing import Any

CATALOG: dict[str, dict[str, dict[str, Any]]] = {
    "action": {
        "schedule_queue_compacted": {"label": "压紧排程队列", "category": "schedule"},
        "historical_timeslot_repaired": {"label": "修复历史时间段", "category": "schedule"},
        "schedule_generated": {"label": "生成排程", "category": "schedule"},
        "schedule_rescheduled": {"label": "重新排程", "category": "schedule"},
    },
    "field": {
        "instrument_code": {"label": "仪器编号", "type": "technical"},
        "previous_task_id": {"label": "前序任务", "type": "entity"},
        "previous_last_slot_id": {"label": "前序任务末时间段", "type": "technical"},
        "moved_slots": {"label": "调整的时间段", "type": "slot_changes"},
        "reason": {"label": "原因", "type": "text"},
        "previous_slot_id": {"label": "前一时间段", "type": "datetime"},
        "before_plan_start": {"label": "调整前开始时间", "type": "datetime"},
        "after_plan_start": {"label": "调整后开始时间", "type": "datetime"},
        "success": {"label": "结果", "type": "status"},
    },
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
        return _fallback_field(str(value))
    return fallback or "其他信息"

def _fallback_action(value: str) -> str:
    prefixes = {"create": "新增", "created": "新增", "update": "修改", "updated": "修改", "delete": "删除", "deleted": "删除", "start": "开始", "started": "开始", "pause": "暂停", "paused": "暂停", "complete": "完成", "completed": "完成", "interrupt": "中断", "repair": "修复", "repaired": "修复", "generate": "生成", "generated": "生成", "reschedule": "重新排程", "rescheduled": "重新排程"}
    words = value.lower().split("_")
    verb = next((prefixes[word] for word in words if word in prefixes), "系统操作")
    noun = "任务" if any(word in words for word in ("task", "timeslot", "slot")) else "排程" if "schedule" in words else "系统数据"
    return f"{verb}{noun}" if verb != "系统操作" else "系统操作"

def _fallback_field(value: str) -> str:
    known = {"id": "编号", "code": "编码", "name": "名称", "status": "状态", "reason": "原因", "path": "请求路径", "duration": "耗时", "created_at": "创建时间", "updated_at": "更新时间"}
    parts = value.lower().split("_")
    return "".join(known.get(part, "") for part in parts) or "其他字段"

def format_value(key: str, value: Any) -> Any:
    """按字段类型转换为可直接渲染/导出的中文值，避免 JS [object Object]。"""
    meta = CATALOG["field"].get(key, {})
    if meta.get("type") == "datetime" and value:
        return _minute_datetime(value)
    if meta.get("type") == "slot_changes" and isinstance(value, list):
        return [
            {"时间段": item.get("slot_id", ""), "原计划": item.get("before", ""), "新计划": item.get("after", "")}
            if isinstance(item, dict) else str(item) for item in value
        ]
    if isinstance(value, str) and len(value) >= 16 and value[4] == "-" and value[7] == "-" and ("T" in value or " " in value):
        return _minute_datetime(value)
    return value

def _minute_datetime(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value[:16].replace("T", " ") if len(value) >= 16 else value

def catalog_payload() -> dict[str, dict[str, dict[str, Any]]]:
    return CATALOG

from __future__ import annotations

from app.services.translation_catalog import CATALOG, format_value, label


CATEGORY_LABELS = {
    "account": "账号与权限",
    "project": "项目与计划",
    "task": "任务管理",
    "schedule": "排程与执行",
    "resource": "仪器与资源",
    "system": "系统配置",
    "other": "其他操作",
}

ACTION_LABELS = {
    "user_logged_in": "用户登录",
    "user_created": "新增用户",
    "user_updated": "修改用户",
    "user_deleted": "删除用户",
    "user_password_reset": "重置密码",
    "project_created": "新增项目",
    "project_updated": "修改项目",
    "project_deleted": "删除项目",
    "project_plan_drafts_committed": "保存项目计划",
    "task_created": "新增任务",
    "task_updated": "修改任务",
    "task_deleted": "删除任务",
    "task_reordered": "调整任务顺序",
    "task_paused": "暂停任务",
    "task_completed": "完成任务",
    "task_started": "开始任务",
    "task_interrupted": "中断任务",
    "task_night_run": "启动夜间运行",
    "notification_read": "标记通知已读",
    "user_logged_out": "用户退出登录", "session_keep_alive": "会话保活",
    "instrument_updated": "修改仪器", "alert_rule_updated": "修改提醒规则",
    "schedule_rule_updated": "修改排程规则", "role_permission_updated": "修改角色权限",
    "historical_timeslot_repaired": "修复历史时间段",
    "task_delay_reported": "提交延期",
    "schedule_generated": "生成排程",
    "schedule_rescheduled": "重新排程",
    "schedule_insert_confirmed": "确认插单",
    "approval_gate_submitted": "提交方案",
    "approval_gate_approved": "确认方案",
    "HTTP POST": "新增/提交",
    "HTTP PUT": "修改",
    "HTTP PATCH": "修改",
    "HTTP DELETE": "删除",
}
ACTION_LABELS.update({key: item["label"] for key, item in CATALOG["action"].items()})

TARGET_LABELS = {
    "project": "项目",
    "schedule": "排程",
    "task": "任务",
    "time_slot": "任务排程时间段",
    "approval_gate": "方案签批",
    "user": "系统用户",
    "instrument": "仪器",
    "instrument_fault": "仪器故障",
    "calendar": "工作日历",
    "api_request": "系统操作",
}


def audit_log_categories() -> list[dict[str, str]]:
    return [
        {"value": key, "label": value}
        for key, value in CATEGORY_LABELS.items()
        if key != "other"
    ]


def present_audit_record(record: dict) -> dict:
    detail = record.get("detail") or {}
    category = detail.get("category") or _legacy_category(record)
    result = detail.get("result") or ("success" if detail.get("success", True) else "failed")
    target_display = detail.get("target_display") or detail.get("task_display") or _target_text(record)
    action = _business_action(record.get("action"), detail.get("path"))
    action_label = label("action", action, "未知操作") if action else "未知操作"
    legacy_summary = detail.get("summary")
    if legacy_summary in {"操作项目任务", "操作系统数据", "修改系统数据", "新增/提交", "系统操作"}:
        legacy_summary = None
    return {
        **record,
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"]),
        "action_label": action_label,
        "target_display": target_display,
        "summary": legacy_summary or _legacy_summary({**record, "action": action}, action_label, target_display),
        "result": result,
        "changes": detail.get("changes") or [],
        "business_detail": _business_detail(detail),
        "technical_detail": _technical_detail(detail),
    }


def _business_action(action: str | None, path: str | None) -> str | None:
    """把旧 HTTP 审计记录还原成用户真正执行的业务动作。"""
    if not path or not action:
        return action
    if "/projects/tasks/" in path:
        return {"HTTP POST": "task_created", "HTTP PUT": "task_updated", "HTTP DELETE": "task_deleted"}.get(action, "task_updated")
    endpoint_actions = {
        "/complete": "task_completed", "/pause": "task_paused", "/start": "task_started",
        "/interrupt": "task_interrupted", "/delay": "task_delay_reported", "/night-run": "task_night_run",
        "/notifications/": "notification_read",
        "/users/login": "user_logged_in", "/users/logout": "user_logged_out",
        "/users/keep-alive": "session_keep_alive", "/wecom-auth/login": "user_logged_in",
        "/schedules/apply-project-plan/confirm-insert": "schedule_insert_confirmed",
        "/schedules/apply-project-plan": "schedule_generated", "/schedules/insert-order": "schedule_insert_confirmed",
        "/approval-gates/": "approval_gate_approved", "/detection-tasks/": "task_updated",
        "/detection-tasks": "task_created", "/projects/tasks/": "task_updated",
        "/projects/": "project_updated", "/instruments/": "instrument_updated",
        "/alert-rules/": "alert_rule_updated", "/schedule-rules/": "schedule_rule_updated",
        "/role-permissions/": "role_permission_updated",
    }
    for suffix, event in endpoint_actions.items():
        if suffix in path:
            return event
    return action


def _legacy_category(record: dict) -> str:
    target_type = record.get("target_type")
    action = record.get("action") or ""
    path = str((record.get("detail") or {}).get("path") or "")
    if target_type == "user" or "/users" in path or "/role-permissions" in path:
        return "account"
    if target_type in {"task", "approval_gate"} or "/tasks" in path or "/approval-gates" in path:
        return "task"
    if target_type in {"schedule", "time_slot"} or "schedule" in action or "/schedules" in path:
        return "schedule"
    if target_type in {"instrument", "instrument_fault"} or "/instruments" in path:
        return "resource"
    if target_type == "project" or "/projects" in path:
        return "project"
    if target_type == "calendar" or any(part in path for part in ["/calendar", "/schedule-rules", "/alert-rules"]):
        return "system"
    if "/notifications/" in path:
        return "system"
    return "other"


def _target_text(record: dict) -> str:
    path = str((record.get("detail") or {}).get("path") or "")
    if "/notifications/" in path:
        target = "通知"
        return f"{target} #{record['target_id']}" if record.get("target_id") else target
    target = TARGET_LABELS.get(record.get("target_type"), "操作对象")
    return f"{target} #{record['target_id']}" if record.get("target_id") else target


def _legacy_summary(record: dict, action_label: str, target_display: str) -> str:
    detail = record.get("detail") or {}
    action_summaries = {
        "task_paused": "暂停任务",
        "task_started": "开始任务",
        "task_completed": "完成任务",
        "task_interrupted": "中断任务",
        "task_delay_reported": "提交任务延期",
        "task_night_run": "设置夜间运行",
    }
    if record.get("action") in action_summaries:
        is_failed = detail.get("result", "success") != "success" or not detail.get("success", True)
        if is_failed:
            reason = detail.get("reason")
            suffix = f"：{reason}" if reason else ""
            return f"{action_summaries[record['action']]}【{target_display}】 · 失败{suffix}"
        if not detail.get("reason"):
            return f"{action_summaries[record['action']]}【{target_display}】 · 成功"
    if record.get("action") == "user_logged_in":
        return f"用户登录【{target_display}】"
    if record.get("action") == "user_logged_out":
        return f"用户退出登录【{target_display}】"
    if detail.get("insert_summary"):
        return str(detail["insert_summary"])
    if detail.get("reason") and target_display:
        return f"{action_label}【{target_display}】：{detail['reason']}"
    if detail.get("path"):
        return f"{_path_action(detail['path'], record.get('action'))} · {'成功' if detail.get('success') else '失败'}"
    return f"{action_label}【{target_display}】"


def _path_action(path: str, action: str | None) -> str:
    if "/notifications/" in path:
        return "标记通知已读"
    method = {"HTTP POST": "提交", "HTTP PUT": "修改", "HTTP PATCH": "修改", "HTTP DELETE": "删除"}.get(action, "操作")
    if "/projects/tasks/" in path:
        return f"{method}项目任务"
    if path.endswith("/tasks/reorder"):
        return "调整任务顺序"
    if "/timeslots/" in path:
        return f"{method}任务执行状态"
    return f"{method}系统数据"


def _business_detail(detail: dict) -> dict:
    hidden = {
        "event_version", "category", "summary", "result", "changes",
        "target_display", "task_display", "path", "status", "success",
        "client_ip", "duration_ms",
    }
    return {label("field", key, key): format_value(key, value) for key, value in detail.items() if key not in hidden and value not in (None, "", [], {})}


def _technical_detail(detail: dict) -> dict:
    labels = {"path": "请求路径", "status": "状态码", "duration_ms": "耗时(ms)", "client_ip": "来源 IP"}
    return {labels[key]: detail[key] for key in labels if detail.get(key) not in (None, "")}

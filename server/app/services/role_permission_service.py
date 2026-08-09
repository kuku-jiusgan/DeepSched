from app.models import RolePermission


ROLES = ["系统管理员", "项目管理员", "分析所所长", "技术组长", "技术员"]
ADMIN_ROLE = "系统管理员"
PAGE_CATALOG = [
    ("/operations/cockpit", "首页", "运营数据中台", []),
    ("/dashboard", "核心 KPI 仪表盘", "运营数据中台", []),
    ("/operations/reports", "精细化运营报表", "运营数据中台", []),
    ("/operations/lab-status", "实验室状态大屏", "运营数据中台", []),
    ("/kanban/instrument-gantt", "仪器甘特图", "资源看板", []),
    ("/kanban/project-gantt", "项目甘特图", "资源看板", []),
    ("/kanban/project-progress", "项目进度", "资源看板", []),
    ("/kanban/human-gantt", "人力甘特图", "资源看板", []),
    ("/tasks/workspace", "个人工作台", "任务管理", [("start", "开始任务"), ("complete", "完成任务"), ("pause", "暂停任务"), ("interrupt", "中断任务"), ("delay", "报告延期"), ("night_run", "夜间运行"), ("submit", "提交方案"), ("approve", "确认方案"), ("confirm_impact", "确认排程影响")]),
    ("/tasks/agenda", "我的安排", "任务管理", [("start", "开始任务"), ("complete", "完成任务"), ("pause", "暂停任务"), ("delay", "报告延期")]),
    ("/tasks/approvals", "方案签批查询", "任务管理", []),
    ("/tasks/faults", "故障提报", "任务管理", [("create", "提报故障"), ("resolve", "解除故障")]),
    ("/projects/detection-tasks", "检测任务管理", "项目管理", [("create", "新建"), ("edit", "编辑"), ("delete", "删除")]),
    ("/projects/ledger", "项目台账管理", "项目管理", [("create", "新建项目"), ("edit", "编辑项目"), ("delete", "删除项目"), ("task_edit", "维护任务")]),
    ("/projects/plan-breakdown", "项目计划拆解", "项目管理", [("create_task", "添加任务"), ("edit_task", "编辑任务"), ("delete_task", "删除任务"), ("import_template", "模板导入"), ("approval_gate", "添加方案签批"), ("save_draft", "仅保存计划"), ("schedule", "保存并排程")]),
    ("/projects/process-dag", "标准工序依赖配置", "项目管理", []),
    ("/projects/resource-ledger", "仪器基础信息", "项目管理", [("create", "添加仪器"), ("edit", "编辑仪器"), ("delete", "删除仪器"), ("manage_capabilities", "维护能力标签"), ("manage_maintenance", "维护仪器窗口")]),
    ("/schedule/rules", "排程规则配置", "排程管理", [("edit", "修改规则"), ("toggle", "启用/停用规则")]),
    ("/schedule/engine", "自动排程引擎", "排程管理", [("generate", "生成排程"), ("reschedule", "重新优化"), ("manual_edit", "手动调整"), ("daily_roll", "执行日常滚动")]),
    ("/schedule/insert-order", "插单代价计算", "排程管理", [("preview", "计算影响"), ("confirm", "确认插单")]),
    ("/system/alerts", "智能预警推送", "系统管理", [("edit_rule", "修改预警规则"), ("edit_channel", "修改推送通道")]),
    ("/system/audit-logs", "操作日志", "系统管理", []),
    ("/system/users", "用户管理", "系统管理", [("create", "添加用户"), ("edit", "编辑用户"), ("password", "修改密码"), ("delete", "删除用户")]),
    ("/system/roles", "角色管理", "系统管理", [("save", "保存权限")]),
    ("/system/basic", "标准任务类型", "系统管理", [("create", "新增类型"), ("edit", "编辑类型"), ("toggle", "启用/停用"), ("delete", "删除类型")]),
    ("/system/calendar", "工作日历管理", "系统管理", []),
]
PAGE_KEYS = {item[0] for item in PAGE_CATALOG}
PAGE_ACTIONS = {key: actions for key, _name, _group, actions in PAGE_CATALOG}
MANAGEMENT_ROLES = {"系统管理员", "项目管理员", "分析所所长", "技术组长"}
READ_ONLY_ROLE_PAGES = {
    "项目管理员": {"/tasks/workspace", "/tasks/agenda", "/projects/detection-tasks"},
}


def _is_read_only_role_page(role: str, page_key: str) -> bool:
    return page_key in READ_ONLY_ROLE_PAGES.get(role, set())
ANALYST_PREFIXES = ("/operations/", "/kanban/", "/tasks/", "/projects/")


class RolePermissionInvalidError(Exception):
    pass


def permission_for(db, role: str, page_key: str) -> dict:
    if role == ADMIN_ROLE:
        return {"can_view": True, "can_operate": True, "action_permissions": _all_actions(page_key, True)}
    row = db.query(RolePermission).filter(
        RolePermission.role == role,
        RolePermission.page_key == page_key,
    ).first()
    if row:
        actions = row.action_permissions
        if not isinstance(actions, dict):
            actions = _all_actions(page_key, bool(row.can_operate))
        actions = _normalized_actions(page_key, actions)
        if _is_read_only_role_page(role, page_key):
            actions = _all_actions(page_key, False)
        return {"can_view": bool(row.can_view), "can_operate": any(actions.values()), "action_permissions": actions}
    return _default_permission(role, page_key)


def permissions_for_role(db, role: str) -> list[dict]:
    return [
        {"page_key": key, "page_name": name, "group_name": group, "actions": [
            {"action_key": action_key, "action_name": action_name, "allowed": current["action_permissions"].get(action_key, False)}
            for action_key, action_name in actions
        ], **{k: v for k, v in current.items() if k != "action_permissions"}}
        for key, name, group, actions in PAGE_CATALOG
        for current in [permission_for(db, role, key)]
    ]


def permissions_for_roles(db, roles: list[str]) -> list[dict]:
    role_permissions = [permissions_for_role(db, role) for role in roles]
    if not role_permissions:
        return permissions_for_role(db, "技术员")
    result = []
    for index, base in enumerate(role_permissions[0]):
        combined = {**base, "actions": [dict(action) for action in base["actions"]]}
        combined["can_view"] = any(items[index]["can_view"] for items in role_permissions)
        combined["can_operate"] = any(items[index]["can_operate"] for items in role_permissions)
        for action_index, action in enumerate(combined["actions"]):
            action["allowed"] = any(items[index]["actions"][action_index]["allowed"] for items in role_permissions)
        result.append(combined)
    return result


def action_allowed_for_roles(db, roles: list[str], page_key: str, action_key: str) -> bool:
    return any(action_allowed(db, role, page_key, action_key) for role in roles)


def save_role_permissions(db, role: str, permissions: list) -> list[dict]:
    if role not in ROLES:
        raise RolePermissionInvalidError("角色不存在")
    if role == ADMIN_ROLE:
        raise RolePermissionInvalidError("系统管理员权限不可修改")
    supplied_keys = {item.page_key for item in permissions}
    if supplied_keys != PAGE_KEYS:
        raise RolePermissionInvalidError("权限页面列表不完整，请刷新后重试")
    existing = {
        row.page_key: row for row in db.query(RolePermission).filter(RolePermission.role == role).all()
    }
    for item in permissions:
        expected_actions = {key for key, _name in PAGE_ACTIONS[item.page_key]}
        supplied_actions = {action.action_key for action in item.actions}
        if supplied_actions != expected_actions:
            raise RolePermissionInvalidError(f"页面 {item.page_key} 的按钮权限列表不完整")
        row = existing.get(item.page_key) or RolePermission(role=role, page_key=item.page_key)
        row.can_view = bool(item.can_view or item.can_operate)
        row.action_permissions = {
            action.action_key: bool(action.allowed) for action in item.actions
        }
        if _is_read_only_role_page(role, item.page_key):
            row.action_permissions = _all_actions(item.page_key, False)
        row.can_operate = any(row.action_permissions.values())
        row.can_view = bool(item.can_view or row.can_operate)
        db.add(row)
    db.commit()
    return permissions_for_role(db, role)


def _default_permission(role: str, page_key: str) -> dict:
    if role in MANAGEMENT_ROLES:
        can_view = page_key not in {"/system/users", "/system/roles", "/schedule/engine"}
        if _is_read_only_role_page(role, page_key):
            return {"can_view": True, "can_operate": False, "action_permissions": _all_actions(page_key, False)}
        return {"can_view": can_view, "can_operate": can_view, "action_permissions": _all_actions(page_key, can_view)}
    can_view = page_key in {"/operations/cockpit", "/dashboard"} or page_key.startswith(ANALYST_PREFIXES)
    can_operate = page_key in {"/tasks/workspace", "/tasks/faults"}
    return {"can_view": can_view, "can_operate": can_operate, "action_permissions": _all_actions(page_key, can_operate)}


def action_allowed(db, role: str, page_key: str, action_key: str) -> bool:
    permission = permission_for(db, role, page_key)
    return permission["can_view"] and permission["action_permissions"].get(action_key, False)


def _all_actions(page_key: str, allowed: bool) -> dict[str, bool]:
    return {action_key: allowed for action_key, _name in PAGE_ACTIONS.get(page_key, [])}


LEGACY_ACTION_INHERITANCE = {
    "/tasks/workspace": {"pause": "complete"},
    "/projects/plan-breakdown": {"save_draft": "create_task"},
    "/projects/resource-ledger": {"manage_capabilities": "edit", "manage_maintenance": "edit"},
    "/schedule/rules": {"toggle": "edit"},
    "/schedule/engine": {"daily_roll": "generate"},
}


def _normalized_actions(page_key: str, actions: dict) -> dict[str, bool]:
    inherited_actions = LEGACY_ACTION_INHERITANCE.get(page_key, {})
    return {
        action_key: bool(actions.get(action_key, actions.get(inherited_actions[action_key], False)))
        if action_key in inherited_actions else bool(actions.get(action_key, False))
        for action_key, _action_name in PAGE_ACTIONS[page_key]
    }

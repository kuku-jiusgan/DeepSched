"""能不能删：任务、检测任务、项目共用的一道判据。

此前三处各写各的，判据、文案和豁免规则都不一样，而且都只看"已完成"、不看"已开始"：
非系统管理员可以把一个正在跑的检测任务、一个进行中的项目直接删掉。少数进行中的任务
确实删不掉，但那是被"已完成"这条判据误伤的——`schedule_lock_status` 判已完成时会看
有没有任何一个时间槽是 completed，一个被切成多段、前几段已做完的进行中任务就会命中，
于是操作人看到的是"已完成任务不允许删除"，而任务在他眼里明明还在跑。

现在统一成一句话：**已开始的东西非系统管理员不许删，已完成的东西只有系统管理员能删**，
并且报错文案照实说是"已开始"还是"已完成"。判断"开始"和"完成"复用
project_status_service 的口径，不另起一套。
"""

from __future__ import annotations

from app.models import Project, Task
from app.services.project_status_service import (
    COMPLETED_TASK_STATUSES,
    STARTED_TASK_STATUSES,
    calculate_project_status,
)


COMPLETED = "completed"
STARTED = "started"
FREE = "free"


def task_tree_state(task: Task) -> str:
    """任务及其子任务整棵树的状态：已完成 / 已开始 / 都不是。"""
    tree = _task_tree(task)
    if any(item.status in COMPLETED_TASK_STATUSES for item in tree):
        return COMPLETED
    if any(_task_has_started(item) for item in tree):
        return STARTED
    return FREE


def project_state(project: Project) -> str:
    """项目（含检测任务）的状态，与首页、报表用的是同一个 calculate_project_status。"""
    status = calculate_project_status(project)
    if status == "completed":
        return COMPLETED
    return STARTED if status == "active" else FREE


def deletion_block_reason(
    state: str,
    *,
    is_system_admin: bool,
    subject: str,
    admin_may_delete_completed: bool = True,
) -> str | None:
    """不允许删除时给出理由，允许则返回 None。

    admin_may_delete_completed=False 用于项目：已结题的项目谁都不能删，系统管理员
    也不行。这是原有规矩，本次统一判据时原样保留。
    """
    if state == COMPLETED:
        if is_system_admin and admin_may_delete_completed:
            return None
        return f"已完成的{subject}不允许删除"
    if state == STARTED and not is_system_admin:
        return f"已开始的{subject}不允许删除，请联系系统管理员"
    return None


def _task_tree(task: Task) -> list[Task]:
    tree: list[Task] = []
    pending = [task]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current.id in seen:
            continue
        seen.add(current.id)
        tree.append(current)
        pending.extend(current.children or [])
    return tree


def _task_has_started(task: Task) -> bool:
    if task.status in STARTED_TASK_STATUSES:
        return True
    return any(slot.actual_start is not None for slot in task.time_slots or [])

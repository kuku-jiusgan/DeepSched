from app.models import Notification, Project, Task, TimeSlot
from app.services.user_role_service import has_any_role


MANAGEMENT_ROLES = {"系统管理员", "项目管理员", "分析所所长", "技术组长"}


class AccessDeniedError(Exception):
    pass


class AccessResourceNotFoundError(Exception):
    pass


def is_management_user(user) -> bool:
    return has_any_role(user, MANAGEMENT_ROLES)


def require_management_user(user) -> None:
    if not is_management_user(user):
        raise AccessDeniedError("当前账号无权执行管理操作")


def require_project_editor(db, project_id: int, user) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise AccessResourceNotFoundError("项目不存在")
    if not is_management_user(user) and project.manager_id != user.id:
        raise AccessDeniedError("无权修改该项目")
    return project


def require_task_editor(db, task_id: int, user) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise AccessResourceNotFoundError("任务不存在")
    require_project_editor(db, task.project_id, user)
    return task


def require_slot_operator(db, slot_id: int, user) -> TimeSlot:
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if slot is None:
        raise AccessResourceNotFoundError("时间槽不存在")
    task = db.query(Task).filter(Task.id == slot.task_id).first()
    if task is None:
        raise AccessResourceNotFoundError("任务不存在")
    if task.assignee_id != user.id:
        raise AccessDeniedError("只能操作本人负责的任务")
    return slot


def require_notification_owner(db, notification_id: int, user) -> Notification:
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if notification is None:
        raise AccessResourceNotFoundError("通知不存在")
    if not is_management_user(user) and notification.user_name != user.username:
        raise AccessDeniedError("无权操作该通知")
    return notification

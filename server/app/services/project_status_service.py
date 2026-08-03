from app.models import Project, Task


COMPLETED_TASK_STATUSES = {"done", "completed"}
STARTED_TASK_STATUSES = {"running", "paused", "done", "completed", "interrupted"}


def calculate_project_status(project: Project) -> str:
    tasks = _leaf_tasks(project.tasks or [])
    if not tasks:
        return "pending"
    if all(task.status in COMPLETED_TASK_STATUSES for task in tasks):
        return "completed"
    if any(_task_has_started(task) for task in tasks):
        return "active"
    return "pending"


def _leaf_tasks(tasks: list[Task]) -> list[Task]:
    parent_ids = {task.parent_id for task in tasks if task.parent_id is not None}
    return [task for task in tasks if task.id not in parent_ids]


def _task_has_started(task: Task) -> bool:
    if task.status in STARTED_TASK_STATUSES:
        return True
    return any(slot.actual_start is not None for slot in task.time_slots or [])

"""删除项目。

原先这段逻辑写在路由里（app/api/projects.py），一路 db.query(...).delete() 手工
清关联表，既违反"路由层不写 SQL"的约定，也漏清了好几张：任务的父子关系没解开，
批量删任务时父行先删就撞上 task.parent_id 自引用外键；仪器桥接预留、夜间运行记录
同样没人管。实测删测试项目A、测试项目B、XM2026199 全部报 IntegrityError 1451。

现在任务连同其时间轴痕迹的清理统一走 task_purge_service，与删单个任务用的是同一
套顺序；这里只负责项目自己的事：能不能删、审计、里程碑和项目行。
"""

from __future__ import annotations

from app.models import Milestone, Project, Task
from app.services.audit_log_service import record_audit_log
from app.services.instrument_status_service import refresh_instrument_statuses
from app.services.project_status_service import calculate_project_status
from app.services.task_purge_service import purge_task_trees


class ProjectDeleteNotFoundError(Exception):
    pass


class ProjectDeleteInvalidError(Exception):
    pass


def delete_project_plan(db, project_id: int, actor_name: str | None = None) -> None:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ProjectDeleteNotFoundError("项目不存在")
    if calculate_project_status(project) == "completed":
        raise ProjectDeleteInvalidError("已完成项目不允许删除")
    task_ids = {
        task_id for task_id, in db.query(Task.id).filter(Task.project_id == project_id).all()
    }
    instrument_ids = purge_task_trees(db, task_ids)
    refresh_instrument_statuses(db, instrument_ids)
    # 任务已被批量删掉，会话里那份 project.tasks 还是旧的；不失效的话
    # db.delete(project) 会照着它再发一遍 DELETE，SQLAlchemy 随即警告
    # "expected to delete 1 row(s); 0 were matched"。
    db.expire(project, ["tasks"])
    db.query(Milestone).filter(Milestone.project_id == project_id).delete(
        synchronize_session=False,
    )
    if actor_name:
        record_audit_log(db, actor_name, "project_deleted", "project", project.id, {
            "project_code": project.code,
            "project_name": project.name,
            "task_count": len(task_ids),
        })
    # 排程失败留下的「调整方案」作业由 Project.deadline_recommendation_jobs 的
    # 级联跟着一起走，见 models.Project。
    db.delete(project)
    db.commit()

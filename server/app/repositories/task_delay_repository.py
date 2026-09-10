from app.models import AuditLog


def has_reported_task_delay(db, task_id: int) -> bool:
    return db.query(AuditLog.id).filter(
        AuditLog.action == "task_delay_reported",
        AuditLog.detail["task_id"].as_integer() == task_id,
    ).first() is not None

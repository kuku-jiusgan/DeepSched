from app.models import Task


def lock_task(db, task_id: int):
    """Lock one task for execution-state transitions.

    SQLite does not support SELECT FOR UPDATE, so unit tests use a plain query.
    Production MySQL keeps the row lock until the surrounding transaction ends.
    """
    query = db.query(Task).filter(Task.id == task_id)
    if db.get_bind().dialect.name == "sqlite":
        return query.first()
    return query.with_for_update().first()

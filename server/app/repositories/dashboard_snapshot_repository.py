from datetime import datetime

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import DashboardStatsSnapshot


def upsert_dashboard_snapshot(db, cache_key: str, payload: dict) -> None:
    values = dict(cache_key=cache_key, payload=payload, generated_at=datetime.now())
    dialect = db.get_bind().dialect.name
    if dialect == "mysql":
        statement = mysql_insert(DashboardStatsSnapshot).values(**values)
        statement = statement.on_duplicate_key_update(
            payload=statement.inserted.payload,
            generated_at=statement.inserted.generated_at,
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(DashboardStatsSnapshot).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "payload": statement.excluded.payload,
                "generated_at": statement.excluded.generated_at,
            },
        )
    else:
        raise ValueError(f"统计快照不支持数据库类型：{dialect}")
    db.execute(statement)

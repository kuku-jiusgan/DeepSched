from datetime import datetime, timedelta

from app.models import DashboardStatsSnapshot
from app.repositories.dashboard_snapshot_repository import upsert_dashboard_snapshot


SNAPSHOT_TTL = timedelta(minutes=1)
SNAPSHOT_RETENTION = timedelta(days=30)


def load_dashboard_snapshot(db, cache_key: str):
    snapshot = db.query(DashboardStatsSnapshot).filter(
        DashboardStatsSnapshot.cache_key == cache_key,
        DashboardStatsSnapshot.generated_at >= datetime.now() - SNAPSHOT_TTL,
    ).first()
    return snapshot.payload if snapshot else None


def load_latest_dashboard_snapshot(db, cache_key: str):
    snapshot = db.query(DashboardStatsSnapshot).filter(
        DashboardStatsSnapshot.cache_key == cache_key,
    ).first()
    return snapshot.payload if snapshot else None


def save_dashboard_snapshot(db, cache_key: str, payload: dict) -> None:
    upsert_dashboard_snapshot(db, cache_key, payload)
    db.query(DashboardStatsSnapshot).filter(
        DashboardStatsSnapshot.generated_at < datetime.now() - SNAPSHOT_RETENTION,
    ).delete(synchronize_session=False)
    db.commit()

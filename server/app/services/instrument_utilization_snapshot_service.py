from datetime import datetime, timedelta

from app.models import InstrumentUtilizationSnapshot
from app.repositories.instrument_utilization_snapshot_repository import (
    upsert_instrument_utilization_snapshot,
)


SNAPSHOT_TTL = timedelta(minutes=1)


def load_utilization_snapshot(db, cache_key: str, allow_stale: bool = False):
    query = db.query(InstrumentUtilizationSnapshot).filter(
        InstrumentUtilizationSnapshot.cache_key == cache_key,
    )
    if not allow_stale:
        query = query.filter(
            InstrumentUtilizationSnapshot.generated_at >= datetime.now() - SNAPSHOT_TTL,
        )
    snapshot = query.first()
    return snapshot.payload if snapshot else None


def save_utilization_snapshot(db, cache_key: str, payload: list[dict]) -> None:
    upsert_instrument_utilization_snapshot(db, cache_key, payload)

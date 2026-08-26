from datetime import datetime, timedelta

from app.models import LabStatusSnapshot


SNAPSHOT_KEY = "default"
SNAPSHOT_TTL = timedelta(seconds=15)


def load_lab_status_snapshot(db):
    snapshot = db.query(LabStatusSnapshot).filter(
        LabStatusSnapshot.cache_key == SNAPSHOT_KEY,
        LabStatusSnapshot.generated_at >= datetime.now() - SNAPSHOT_TTL,
    ).first()
    return snapshot.payload if snapshot else None


def save_lab_status_snapshot(db, payload: list[dict]) -> None:
    snapshot = db.query(LabStatusSnapshot).filter(
        LabStatusSnapshot.cache_key == SNAPSHOT_KEY,
    ).first()
    if snapshot is None:
        db.add(LabStatusSnapshot(cache_key=SNAPSHOT_KEY, payload=payload))
    else:
        snapshot.payload = payload
        snapshot.generated_at = datetime.now()
    db.commit()

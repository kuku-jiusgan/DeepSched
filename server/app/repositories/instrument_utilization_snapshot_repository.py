from datetime import datetime

from sqlalchemy.dialects.mysql import insert

from app.models import InstrumentUtilizationSnapshot


def upsert_instrument_utilization_snapshot(
    db,
    cache_key: str,
    payload: list[dict],
) -> None:
    generated_at = datetime.now()
    if db.bind.dialect.name == "mysql":
        statement = insert(InstrumentUtilizationSnapshot).values(
            cache_key=cache_key,
            payload=payload,
            generated_at=generated_at,
        )
        db.execute(statement.on_duplicate_key_update(
            payload=statement.inserted.payload,
            generated_at=statement.inserted.generated_at,
        ))
        db.commit()
        return
    snapshot = db.query(InstrumentUtilizationSnapshot).filter(
        InstrumentUtilizationSnapshot.cache_key == cache_key,
    ).first()
    if snapshot is None:
        db.add(InstrumentUtilizationSnapshot(
            cache_key=cache_key,
            payload=payload,
            generated_at=generated_at,
        ))
    else:
        snapshot.payload = payload
        snapshot.generated_at = generated_at
    db.commit()

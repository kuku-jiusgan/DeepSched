"""Add per-instrument effective working hours.

Revision ID: 20260825_12
Revises: 20260825_11
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "20260825_12"
down_revision = "20260825_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instrument", sa.Column("effective_work_start", sa.String(length=5), nullable=True))
    op.add_column("instrument", sa.Column("effective_work_end", sa.String(length=5), nullable=True))
    op.add_column(
        "schedule_calendar_snapshot",
        sa.Column("instrument_working_hours", sa.JSON(), nullable=True),
    )
    connection = op.get_bind()
    row = connection.execute(sa.text(
        "SELECT params FROM schedule_rule WHERE code = 'working_hours' LIMIT 1"
    )).first()
    params = row[0] if row else {}
    if isinstance(params, str):
        params = json.loads(params)
    start = _parse_time((params or {}).get("day_start"), "08:30")
    end = _parse_time((params or {}).get("day_end"), "20:00")
    connection.execute(
        sa.text(
            "UPDATE instrument SET effective_work_start = :start, effective_work_end = :end "
            "WHERE effective_work_start IS NULL OR effective_work_end IS NULL"
        ),
        {"start": start, "end": end},
    )
    connection.execute(sa.text(
        "UPDATE schedule_calendar_snapshot SET instrument_working_hours = '{}' "
        "WHERE instrument_working_hours IS NULL"
    ))
    op.alter_column("instrument", "effective_work_start", nullable=False)
    op.alter_column("instrument", "effective_work_end", nullable=False)
    op.alter_column("schedule_calendar_snapshot", "instrument_working_hours", nullable=False)


def downgrade() -> None:
    op.drop_column("schedule_calendar_snapshot", "instrument_working_hours")
    op.drop_column("instrument", "effective_work_end")
    op.drop_column("instrument", "effective_work_start")


def _parse_time(value, default: str) -> str:
    if not value:
        return default
    if isinstance(value, (int, float)):
        return f"{int(value):02d}:00"
    hours, minutes = str(value).split(":")[:2]
    return f"{int(hours):02d}:{int(minutes):02d}"

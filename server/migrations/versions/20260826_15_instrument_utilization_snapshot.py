"""Add instrument utilization snapshot table.

Revision ID: 20260826_15
Revises: 20260826_14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_15"
down_revision = "20260826_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_utilization_snapshot",
        sa.Column("cache_key", sa.String(length=200), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_instrument_utilization_snapshot_generated_at", "instrument_utilization_snapshot", ["generated_at"])


def downgrade() -> None:
    op.drop_index("ix_instrument_utilization_snapshot_generated_at", table_name="instrument_utilization_snapshot")
    op.drop_table("instrument_utilization_snapshot")

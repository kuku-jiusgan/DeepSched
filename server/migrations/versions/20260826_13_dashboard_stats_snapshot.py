"""Add dashboard statistics snapshot table.

Revision ID: 20260826_13
Revises: 20260825_12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_13"
down_revision = "20260825_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_stats_snapshot",
        sa.Column("cache_key", sa.String(length=200), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_dashboard_stats_snapshot_generated_at",
        "dashboard_stats_snapshot",
        ["generated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dashboard_stats_snapshot_generated_at", table_name="dashboard_stats_snapshot")
    op.drop_table("dashboard_stats_snapshot")

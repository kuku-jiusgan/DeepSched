"""Add lab status snapshot table.

Revision ID: 20260826_14
Revises: 20260826_13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_14"
down_revision = "20260826_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lab_status_snapshot",
        sa.Column("cache_key", sa.String(length=50), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lab_status_snapshot_generated_at", "lab_status_snapshot", ["generated_at"])


def downgrade() -> None:
    op.drop_index("ix_lab_status_snapshot_generated_at", table_name="lab_status_snapshot")
    op.drop_table("lab_status_snapshot")

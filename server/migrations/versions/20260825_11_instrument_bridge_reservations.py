"""Persist human-task instrument continuity reservations separately.

Revision ID: 20260825_11
Revises: 20260825_10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_11"
down_revision = "20260825_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_bridge_reservation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("schedule_run_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("task.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instrument.id"), nullable=False),
        sa.Column("previous_task_id", sa.Integer(), sa.ForeignKey("task.id", ondelete="CASCADE"), nullable=False),
        sa.Column("following_task_id", sa.Integer(), sa.ForeignKey("task.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_start", sa.DateTime(), nullable=False),
        sa.Column("plan_end", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_instrument_bridge_reservation_instrument_window",
        "instrument_bridge_reservation",
        ["instrument_id", "plan_start", "plan_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_instrument_bridge_reservation_instrument_window", table_name="instrument_bridge_reservation")
    op.drop_table("instrument_bridge_reservation")

"""增加任务暂停与执行片段

Revision ID: 20260803_07
Revises: 20260722_06
"""
from alembic import op
import sqlalchemy as sa


revision = "20260803_07"
down_revision = "20260722_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_execution_segment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("task.id"), nullable=False),
        sa.Column("slot_id", sa.Integer(), sa.ForeignKey("time_slot.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instrument.id")),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("user.id")),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime()),
        sa.Column("end_reason", sa.String(length=20)),
        sa.Column("pause_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )


def downgrade():
    op.drop_table("task_execution_segment")

"""记录延期追加的计划工时

Revision ID: 20260824_09
Revises: 20260803_07
"""
from alembic import op
import sqlalchemy as sa


revision = "20260824_09"
down_revision = "20260803_07"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "task",
        sa.Column("additional_planned_minutes", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("task", "additional_planned_minutes")

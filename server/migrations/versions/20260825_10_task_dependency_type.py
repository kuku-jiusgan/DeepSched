"""Add dependency semantics for continuous task successors.

Revision ID: 20260825_10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_10"
down_revision = "20260824_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_dependency",
        sa.Column("dependency_type", sa.String(length=30), nullable=False, server_default="predecessor"),
    )
    op.execute(
        "UPDATE task_dependency AS dependency "
        "JOIN task AS child ON child.id = dependency.task_id "
        "JOIN task AS parent ON parent.id = dependency.predecessor_id "
        "SET dependency.dependency_type = 'continuous_successor' "
        "WHERE child.task_type IN ('QCFA_001', 'ZXBG_001') "
        "AND parent.task_type IN ('FFKF_001', 'FFYZ_001')"
    )


def downgrade() -> None:
    op.drop_column("task_dependency", "dependency_type")

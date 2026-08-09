"""补充后续功能的按钮权限

Revision ID: 20260805_08
Revises: 20260803_07
"""
from alembic import op
import sqlalchemy as sa


revision = "20260805_08"
down_revision = "20260803_07"
branch_labels = None
depends_on = None


role_permission = sa.table(
    "role_permission",
    sa.column("id", sa.Integer),
    sa.column("role", sa.String),
    sa.column("page_key", sa.String),
    sa.column("can_operate", sa.Boolean),
    sa.column("action_permissions", sa.JSON),
)


LEGACY_ACTION_INHERITANCE = {
    "/tasks/workspace": {"pause": "complete"},
    "/projects/plan-breakdown": {"save_draft": "create_task"},
    "/projects/resource-ledger": {"manage_capabilities": "edit", "manage_maintenance": "edit"},
    "/schedule/rules": {"toggle": "edit"},
    "/schedule/engine": {"daily_roll": "generate"},
}


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            role_permission.c.id,
            role_permission.c.page_key,
            role_permission.c.can_operate,
            role_permission.c.action_permissions,
        ).where(role_permission.c.page_key.in_(LEGACY_ACTION_INHERITANCE))
    ).mappings()
    for row in rows:
        permissions = dict(row["action_permissions"] or {})
        changed = False
        for action_key, legacy_action in LEGACY_ACTION_INHERITANCE[row["page_key"]].items():
            if action_key in permissions:
                continue
            permissions[action_key] = bool(permissions.get(legacy_action, row["can_operate"]))
            changed = True
        if not changed:
            continue
        connection.execute(
            sa.update(role_permission)
            .where(role_permission.c.id == row["id"])
            .values(action_permissions=permissions)
        )
    connection.execute(
        sa.update(role_permission)
        .where(
            role_permission.c.page_key.in_(["/tasks/workspace", "/projects/detection-tasks"]),
            role_permission.c.role == "项目管理员",
        )
        .values(can_operate=False, action_permissions={})
    )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(role_permission.c.id, role_permission.c.page_key, role_permission.c.action_permissions)
        .where(role_permission.c.page_key.in_(LEGACY_ACTION_INHERITANCE))
    ).mappings()
    for row in rows:
        permissions = dict(row["action_permissions"] or {})
        changed = False
        for action_key in LEGACY_ACTION_INHERITANCE[row["page_key"]]:
            if action_key in permissions:
                permissions.pop(action_key)
                changed = True
        if not changed:
            continue
        connection.execute(
            sa.update(role_permission)
            .where(role_permission.c.id == row["id"])
            .values(action_permissions=permissions)
        )

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import RolePermission
from app.services.role_permission_service import (
    PAGE_CATALOG,
    action_allowed,
    permission_for,
    save_role_permissions,
)


class RolePermissionServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_admin_always_has_full_permissions(self):
        value = permission_for(self.db, "系统管理员", "/system/roles")
        self.assertTrue(value["can_view"])
        self.assertTrue(value["can_operate"])
        self.assertTrue(value["action_permissions"]["save"])

    def test_operation_permission_also_enables_view(self):
        permissions = [
            SimpleNamespace(
                page_key=key,
                can_view=False,
                can_operate=key == "/projects/ledger",
                actions=[
                    SimpleNamespace(action_key=action_key, allowed=key == "/projects/ledger")
                    for action_key, _action_name in actions
                ],
            )
            for key, _name, _group, actions in PAGE_CATALOG
        ]

        save_role_permissions(self.db, "技术员", permissions)

        value = permission_for(self.db, "技术员", "/projects/ledger")
        self.assertTrue(value["can_view"])
        self.assertTrue(value["can_operate"])
        self.assertTrue(all(value["action_permissions"].values()))

    def test_buttons_on_same_page_are_authorized_independently(self):
        permissions = [
            SimpleNamespace(
                page_key=key,
                can_view=key == "/projects/detection-tasks",
                can_operate=False,
                actions=[
                    SimpleNamespace(
                        action_key=action_key,
                        allowed=key == "/projects/detection-tasks" and action_key == "edit",
                    )
                    for action_key, _action_name in actions
                ],
            )
            for key, _name, _group, actions in PAGE_CATALOG
        ]
        save_role_permissions(self.db, "技术员", permissions)

        self.assertTrue(action_allowed(self.db, "技术员", "/projects/detection-tasks", "edit"))
        self.assertFalse(action_allowed(self.db, "技术员", "/projects/detection-tasks", "delete"))

    def test_existing_permissions_enable_follow_up_actions(self):
        legacy_permissions = {
            "/tasks/workspace": {"complete": True},
            "/projects/plan-breakdown": {"create_task": True},
            "/projects/resource-ledger": {"edit": True},
            "/schedule/rules": {"edit": True},
            "/schedule/engine": {"generate": True},
        }
        for page_key, actions in legacy_permissions.items():
            self.db.add(RolePermission(
                role="技术组长",
                page_key=page_key,
                can_view=True,
                can_operate=True,
                action_permissions=actions,
            ))
        self.db.commit()

        self.assertTrue(permission_for(self.db, "技术组长", "/tasks/workspace")["action_permissions"]["pause"])
        self.assertTrue(permission_for(self.db, "技术组长", "/projects/plan-breakdown")["action_permissions"]["save_draft"])
        resource_actions = permission_for(self.db, "技术组长", "/projects/resource-ledger")["action_permissions"]
        self.assertTrue(resource_actions["manage_capabilities"])
        self.assertTrue(resource_actions["manage_maintenance"])
        self.assertTrue(permission_for(self.db, "技术组长", "/schedule/rules")["action_permissions"]["toggle"])
        self.assertTrue(permission_for(self.db, "技术组长", "/schedule/engine")["action_permissions"]["daily_roll"])

    def test_project_admin_can_view_workspace_but_cannot_operate(self):
        self.db.add(RolePermission(
            role="项目管理员",
            page_key="/tasks/workspace",
            can_view=True,
            can_operate=True,
            action_permissions={"start": True, "complete": True, "pause": True},
        ))
        self.db.commit()

        value = permission_for(self.db, "项目管理员", "/tasks/workspace")

        self.assertTrue(value["can_view"])
        self.assertFalse(value["can_operate"])
        self.assertTrue(value["action_permissions"])
        self.assertFalse(any(value["action_permissions"].values()))

        detection_value = permission_for(self.db, "项目管理员", "/projects/detection-tasks")
        self.assertTrue(detection_value["can_view"])
        self.assertFalse(detection_value["can_operate"])
        self.assertFalse(any(detection_value["action_permissions"].values()))


if __name__ == "__main__":
    unittest.main()

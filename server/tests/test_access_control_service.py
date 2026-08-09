import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Notification, Project, Task, TimeSlot, User
from app.services.access_control_service import (
    AccessDeniedError,
    AccessResourceNotFoundError,
    require_management_user,
    require_notification_owner,
    require_project_editor,
    require_slot_operator,
    require_task_editor,
)


class AccessControlServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.owner = User(
            username="owner", display_name="负责人", role="分析员", is_active=True
        )
        self.assignee = User(
            username="assignee", display_name="执行人", role="分析员", is_active=True
        )
        self.other = User(
            username="other", display_name="其他人", role="分析员", is_active=True
        )
        self.admin = User(
            username="admin", display_name="管理员", role="系统管理员", is_active=True
        )
        self.db.add_all([self.owner, self.assignee, self.other, self.admin])
        self.db.flush()
        self.project = Project(name="项目", code="P-ACL", manager_id=self.owner.id)
        self.db.add(self.project)
        self.db.flush()
        self.task = Task(
            project_id=self.project.id,
            name="任务",
            task_type="manual",
            assignee_id=self.assignee.id,
        )
        self.db.add(self.task)
        self.db.flush()
        now = datetime.now()
        self.slot = TimeSlot(
            task_id=self.task.id,
            plan_start=now,
            plan_end=now + timedelta(hours=1),
        )
        self.notification = Notification(user_name=self.assignee.username, n_type="test")
        self.db.add_all([self.slot, self.notification])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_management_operation_rejects_regular_user(self):
        with self.assertRaises(AccessDeniedError):
            require_management_user(self.other)
        require_management_user(self.admin)

    def test_project_and_task_edits_are_scoped_to_project_owner(self):
        self.assertEqual(
            self.project.id,
            require_project_editor(self.db, self.project.id, self.owner).id,
        )
        self.assertEqual(self.task.id, require_task_editor(self.db, self.task.id, self.owner).id)
        with self.assertRaises(AccessDeniedError):
            require_project_editor(self.db, self.project.id, self.other)
        with self.assertRaises(AccessDeniedError):
            require_task_editor(self.db, self.task.id, self.assignee)

    def test_slot_operation_only_allows_assignee(self):
        self.assertEqual(
            self.slot.id,
            require_slot_operator(self.db, self.slot.id, self.assignee).id,
        )
        for user in [self.owner, self.admin, self.other]:
            with self.subTest(role=user.role, username=user.username):
                with self.assertRaisesRegex(AccessDeniedError, "只能操作本人负责的任务"):
                    require_slot_operator(self.db, self.slot.id, user)

    def test_notification_operation_is_scoped_to_owner(self):
        self.assertEqual(
            self.notification.id,
            require_notification_owner(self.db, self.notification.id, self.assignee).id,
        )
        require_notification_owner(self.db, self.notification.id, self.admin)
        with self.assertRaises(AccessDeniedError):
            require_notification_owner(self.db, self.notification.id, self.other)

    def test_missing_resource_does_not_fall_through(self):
        with self.assertRaises(AccessResourceNotFoundError):
            require_project_editor(self.db, 999_999, self.admin)
        with self.assertRaises(AccessResourceNotFoundError):
            require_slot_operator(self.db, 999_999, self.admin)
        with self.assertRaises(AccessResourceNotFoundError):
            require_notification_owner(self.db, 999_999, self.admin)


if __name__ == "__main__":
    unittest.main()

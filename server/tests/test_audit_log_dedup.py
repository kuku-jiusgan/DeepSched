import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import AuditLog, User
from app.services.audit_log_service import list_audit_logs


class AuditLogDeduplicationTest(unittest.TestCase):
    """一次业务动作会同时落 api_request 和业务日志，列表里只应留业务日志。"""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(User(username="jiangxx", display_name="江秀秀", role="技术员"))
        self.now = datetime(2026, 9, 10, 9, 0)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _add(self, user_name, action, target_type, target_id, detail, seconds):
        self.db.add(AuditLog(
            user_name=user_name, action=action, target_type=target_type,
            target_id=target_id, detail=detail,
            created_at=self.now + timedelta(seconds=seconds),
        ))
        self.db.commit()

    def test_api_request_with_business_twin_is_hidden(self):
        self._add("江秀秀", "task_updated", "task", 7, {"summary": "修改任务"}, 0)
        self._add("jiangxx", "HTTP PUT", "api_request", None, {"path": "/api/v1/projects/tasks/7"}, 1)

        logs = list_audit_logs(self.db)

        self.assertEqual(["task_updated"], [log.action for log in logs])

    def test_api_request_without_twin_is_kept(self):
        self._add("jiangxx", "HTTP PUT", "api_request", None, {"path": "/api/v1/projects/tasks/7"}, 0)
        self._add("江秀秀", "task_updated", "task", 7, {"summary": "修改任务"}, 30)

        logs = list_audit_logs(self.db)

        self.assertEqual({"HTTP PUT", "task_updated"}, {log.action for log in logs})

    def test_twin_of_another_operator_does_not_hide_request(self):
        self.db.add(User(username="other", display_name="其他用户", role="技术员"))
        self.db.commit()
        self._add("其他用户", "task_updated", "task", 7, {"summary": "修改任务"}, 0)
        self._add("jiangxx", "HTTP PUT", "api_request", None, {"path": "/api/v1/projects/tasks/7"}, 1)

        logs = list_audit_logs(self.db)

        self.assertEqual(2, len(logs))

    def test_twin_on_another_target_does_not_hide_request(self):
        self._add("江秀秀", "task_updated", "task", 9, {"summary": "修改任务"}, 0)
        self._add("jiangxx", "HTTP PUT", "api_request", None, {"path": "/api/v1/projects/tasks/7"}, 1)

        logs = list_audit_logs(self.db)

        self.assertEqual(2, len(logs))

    def test_keep_alive_requests_are_hidden(self):
        self._add("jiangxx", "HTTP POST", "api_request", None, {"path": "/api/v1/users/keep-alive"}, 0)

        self.assertEqual([], list_audit_logs(self.db))


if __name__ == "__main__":
    unittest.main()

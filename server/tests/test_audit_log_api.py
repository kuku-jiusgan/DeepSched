import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.audit_logs import get_audit_logs
from app.core.database import Base
from app.models import AuditLog


class AuditLogApiPaginationTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        now = datetime(2026, 9, 10, 9, 0)
        self.db.add_all([
            AuditLog(
                user_name="江秀秀" if index < 4 else "其他用户",
                action="user_updated",
                target_type="user",
                target_id=index + 1,
                detail={
                    "category": "account",
                    "summary": "修改用户【江秀秀】" if index < 4 else "修改用户【其他用户】",
                    "target_display": "江秀秀" if index < 4 else "其他用户",
                    "result": "success",
                },
                created_at=now - timedelta(minutes=index),
            )
            for index in range(60)
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.api.audit_logs._ensure_audit_log_access")
    def test_keyword_total_matches_filtered_rows(self, _ensure_access):
        response = get_audit_logs(
            keyword="江秀秀",
            start_at=None,
            end_at=None,
            page=1,
            page_size=2,
            db=self.db,
            user=None,
        )

        self.assertEqual(4, response["total"])
        self.assertEqual(2, len(response["items"]))
        self.assertEqual(1, response["page"])

    @patch("app.api.audit_logs._ensure_audit_log_access")
    def test_filtered_second_page_contains_remaining_rows(self, _ensure_access):
        response = get_audit_logs(
            keyword="江秀秀",
            start_at=None,
            end_at=None,
            page=2,
            page_size=3,
            db=self.db,
            user=None,
        )

        self.assertEqual(4, response["total"])
        self.assertEqual(1, len(response["items"]))


if __name__ == "__main__":
    unittest.main()

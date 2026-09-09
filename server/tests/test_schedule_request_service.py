import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, ScheduleEpoch, ScheduleRunRequest
from app.services.schedule_request_service import (
    claim_next_schedule_request,
    enqueue_schedule_request,
    recover_stale_schedule_requests,
)


class ScheduleRequestServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add_all([
            Project(id=1, code="P-1", name="项目", project_kind="project"),
            ScheduleEpoch(id=1, version=7),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_active_request_is_deduplicated(self):
        first = enqueue_schedule_request(self.db, 1, 2)
        second = enqueue_schedule_request(self.db, 1, 3)
        self.assertEqual(first.id, second.id)
        self.assertEqual(7, first.base_schedule_epoch)
        self.assertEqual(1, self.db.query(ScheduleRunRequest).count())

    def test_stale_running_request_returns_to_queue(self):
        request = enqueue_schedule_request(self.db, 1, 2)
        request.status = "running"
        request.heartbeat_at = __import__("datetime").datetime.now() - __import__("datetime").timedelta(seconds=300)
        self.db.commit()

        self.assertEqual(1, recover_stale_schedule_requests(self.db))
        self.db.refresh(request)
        self.assertEqual("queued", request.status)
        self.assertIn("重新排队", request.error_message)

    def test_completed_request_does_not_block_new_request(self):
        first = enqueue_schedule_request(self.db, 1, 2)
        first.status = "succeeded"
        self.db.commit()
        second = enqueue_schedule_request(self.db, 1, 2)
        self.assertNotEqual(first.id, second.id)

    def test_claim_is_idempotent(self):
        request = enqueue_schedule_request(self.db, 1, 2)
        claimed = claim_next_schedule_request(self.db)
        self.assertEqual(request.id, claimed.id)
        self.assertEqual("running", claimed.status)
        self.assertIsNone(claim_next_schedule_request(self.db))


if __name__ == "__main__":
    unittest.main()

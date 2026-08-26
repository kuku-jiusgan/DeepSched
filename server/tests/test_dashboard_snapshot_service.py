import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import DashboardStatsSnapshot
from app.services.dashboard_snapshot_service import load_dashboard_snapshot, save_dashboard_snapshot


class DashboardSnapshotServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_save_replaces_same_key_and_removes_snapshots_older_than_30_days(self):
        self.db.add(DashboardStatsSnapshot(
            cache_key="old",
            payload={"avg_utilization": 1},
            generated_at=datetime.now() - timedelta(days=31),
        ))
        self.db.commit()

        save_dashboard_snapshot(self.db, "current", {"avg_utilization": 2})
        save_dashboard_snapshot(self.db, "current", {"avg_utilization": 3})

        self.assertIsNone(load_dashboard_snapshot(self.db, "old"))
        self.assertEqual({"avg_utilization": 3}, load_dashboard_snapshot(self.db, "current"))
        self.assertEqual(1, self.db.query(DashboardStatsSnapshot).count())


if __name__ == "__main__":
    unittest.main()

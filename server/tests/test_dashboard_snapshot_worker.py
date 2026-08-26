import unittest
from app.services.dashboard_snapshot_worker import _refresh_loop


class DashboardSnapshotWorkerTest(unittest.TestCase):
    def test_worker_module_exposes_refresh_loop(self):
        self.assertTrue(callable(_refresh_loop))


if __name__ == "__main__":
    unittest.main()

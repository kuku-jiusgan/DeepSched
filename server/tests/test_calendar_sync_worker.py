import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import SysCalendar
from app.services.calendar_sync_worker import _has_synced_holidays, _sync_due_years, _years_due


class CalendarSyncWorkerTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_next_year_becomes_due_on_december_first(self):
        self.assertEqual((2026,), _years_due(date(2026, 11, 30)))
        self.assertEqual((2026, 2027), _years_due(date(2026, 12, 1)))

    def test_synced_year_is_not_requested_again(self):
        self.db.add(SysCalendar(
            date=date(2026, 1, 1),
            is_working_day=False,
            day_type="holiday",
            holiday_name="元旦",
            source="sync",
        ))
        self.db.commit()

        self.assertTrue(_has_synced_holidays(self.db, 2026))
        with patch("app.services.calendar_sync_worker.sync_calendar_holidays") as sync:
            self.assertEqual([], _sync_due_years(self.db, date(2026, 8, 4)))
        sync.assert_not_called()

    def test_missing_next_year_is_retried_after_december_first(self):
        with patch("app.services.calendar_sync_worker.sync_calendar_holidays") as sync:
            synced_years = _sync_due_years(self.db, date(2026, 12, 1))

        self.assertEqual([2026, 2027], synced_years)
        self.assertEqual([2026, 2027], [call.args[1] for call in sync.call_args_list])


if __name__ == "__main__":
    unittest.main()

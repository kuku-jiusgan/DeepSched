import unittest
from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import AuditLog, Project, ScheduleCalendarSnapshot, SysCalendar, Task, TimeSlot
from app.services.calendar_service import (
    ensure_calendar_year,
    list_calendar,
    sync_calendar_holidays,
    update_calendar_date,
)
from app.services.schedule_calendar_snapshot_service import save_schedule_calendar_snapshot


class CalendarServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_year_is_persisted_with_weekday_defaults(self):
        created = ensure_calendar_year(self.db, 2026, commit=True)

        self.assertEqual(created, 365)
        august = list_calendar(self.db, 2026, 8)
        august_fourth = next(day for day in august if day.date == date(2026, 8, 4))
        self.assertTrue(august_fourth.is_working_day)
        self.assertEqual(august_fourth.source, "default")

    def test_non_working_change_marks_affected_task_dirty_and_audits(self):
        project = Project(name="测试项目", code="CAL-001")
        self.db.add(project)
        self.db.flush()
        task = Task(project_id=project.id, name="检测", task_type="test", status="scheduled")
        self.db.add(task)
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=task.id,
            instrument_id=None,
            plan_start=datetime(2026, 8, 4, 9),
            plan_end=datetime(2026, 8, 4, 12),
            status="scheduled",
        ))
        self.db.commit()

        day, impact = update_calendar_date(
            self.db,
            date(2026, 8, 4),
            is_working_day=False,
            day_type="holiday",
            holiday_name="测试假日",
            operator_name="admin",
        )

        self.db.refresh(task)
        self.assertEqual(day.source, "manual")
        self.assertTrue(task.schedule_dirty)
        self.assertEqual(impact["affected_task_count"], 1)
        self.assertTrue(impact["needs_reschedule"])
        self.assertEqual(self.db.query(AuditLog).filter(
            AuditLog.action == "calendar_day_updated"
        ).count(), 1)

    def test_holiday_sync_uses_trailing_slash_and_persists_days(self):
        response = BytesIO(b'{"code":0,"holiday":{"01-01":{"holiday":true,"name":"New Year"},"02-07":{"holiday":false,"name":"Adjusted workday"}}}')
        response.__enter__ = lambda value: value
        response.__exit__ = lambda *_args: None

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            sync_calendar_holidays(self.db, 2026, "system")

        request = urlopen.call_args.args[0]
        self.assertEqual("http://timor.tech/api/holiday/year/2026/", request.full_url)
        self.assertEqual("application/json", request.get_header("Accept"))
        self.assertIn("Mozilla/5.0", request.get_header("User-agent"))
        new_year = self.db.query(SysCalendar).filter(SysCalendar.date == date(2026, 1, 1)).one()
        adjusted_workday = self.db.query(SysCalendar).filter(SysCalendar.date == date(2026, 2, 7)).one()
        self.assertFalse(new_year.is_working_day)
        self.assertEqual("holiday", new_year.day_type)
        self.assertEqual("New Year", new_year.holiday_name)
        self.assertEqual("sync", new_year.source)
        self.assertTrue(adjusted_workday.is_working_day)
        self.assertEqual("compensate", adjusted_workday.day_type)
        self.assertEqual("sync", adjusted_workday.source)

    def test_schedule_calendar_snapshot_is_persisted(self):
        save_schedule_calendar_snapshot(
            self.db,
            "run-001",
            datetime(2026, 8, 4, 8, 30),
            datetime(2026, 9, 4, 8, 30),
            {"day_start": "08:30", "day_end": "20:00"},
            {date(2026, 8, 4): {"is_working_day": True, "day_type": "workday"}},
            [(1, (0, 4))],
        )
        self.db.commit()

        snapshot = self.db.query(ScheduleCalendarSnapshot).one()
        self.assertEqual(snapshot.schedule_run_id, "run-001")
        self.assertEqual(snapshot.calendar_days["2026-08-04"]["day_type"], "workday")
        self.assertEqual(snapshot.maintenance_windows[0]["instrument_id"], 1)


if __name__ == "__main__":
    unittest.main()

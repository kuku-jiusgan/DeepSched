import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.scheduler_failure_presentation import build_failure_presentation
from app.services.scheduler_deadline_recommendation import _capacity_lower_date


class SchedulerFailurePresentationTest(unittest.TestCase):
    def test_deadline_search_starts_after_capacity_can_cover_deficit(self):
        start = datetime(2026, 8, 21, 0, 0)
        deadline = datetime(2026, 8, 22, 23, 59)
        prefix = list(range(201))

        result = _capacity_lower_date(
            deadline, start, start + timedelta(days=4), {101: prefix},
            [{"instrument_id": 101, "deficit_hours": 1}],
        )

        self.assertGreater(result, deadline.date())

    def test_aggregates_same_instrument_without_repeating_capacity(self):
        project = SimpleNamespace(
            id=1, code="XM-001", name="当前项目",
            end_date=datetime.now() + timedelta(days=11),
        )
        detail = {
            "project_id": 2, "project_label": "测试项目B",
            "scheduled_hours": 20, "forecast_hours": 14.5,
            "waiting_hours": 0, "total_hours": 34.5,
        }
        second_detail = {
            "project_id": 3, "project_label": "测试项目A",
            "scheduled_hours": 22.5, "forecast_hours": 0,
            "waiting_hours": 0, "total_hours": 22.5,
        }
        groups = [
            {
                "instrument_id": 101, "instrument_label": "测试仪器(CSYQ)",
                "available_hours": 85, "occupied_hours": 34.5,
                "remaining_hours": 50.5, "required_hours": 70,
                "deficit_hours": 19.5, "details": [detail, second_detail],
            },
            {
                "instrument_id": 101, "instrument_label": "测试仪器(CSYQ)",
                "available_hours": 85, "occupied_hours": 34.5,
                "remaining_hours": 50.5, "required_hours": 20,
                "deficit_hours": 0, "details": [detail, second_detail],
            },
        ]

        result = build_failure_presentation(project, groups)

        self.assertEqual(1, len(result["instruments"]))
        instrument = result["instruments"][0]
        self.assertEqual(85, instrument["available_hours"])
        self.assertEqual(34.5, instrument["occupied_hours"])
        self.assertEqual(90, instrument["required_hours"])
        self.assertEqual(39.5, instrument["deficit_hours"])
        self.assertEqual(2, len(result["occupancy"]))
        self.assertEqual([], result["recommendations"])

    def test_recommends_only_project_that_can_cover_full_deficit(self):
        project = SimpleNamespace(
            id=1, code="XM-001", name="当前项目",
            end_date=datetime.now() + timedelta(days=11),
        )
        groups = [{
            "instrument_id": 101, "instrument_label": "测试仪器(CSYQ)",
            "available_hours": 85, "occupied_hours": 50, "remaining_hours": 35,
            "required_hours": 70, "deficit_hours": 35,
            "details": [
                {
                    "project_id": 2, "project_label": "可延期项目",
                    "scheduled_hours": 40, "forecast_hours": 0,
                    "waiting_hours": 0, "total_hours": 40,
                },
                {
                    "project_id": 3, "project_label": "不足项目",
                    "scheduled_hours": 20, "forecast_hours": 0,
                    "waiting_hours": 0, "total_hours": 20,
                },
            ],
        }]

        result = build_failure_presentation(project, groups)

        self.assertEqual(1, len(result["recommendations"]))
        recommendation = result["recommendations"][0]
        self.assertEqual("B", recommendation["code"])
        self.assertEqual(2, recommendation["project_id"])
        self.assertIn("能够覆盖当前仪器缺口", recommendation["description"])


if __name__ == "__main__":
    unittest.main()

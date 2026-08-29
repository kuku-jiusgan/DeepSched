"""项目工时报表按项目状态筛选。

项目状态是按任务实时算出来的，不是数据库列，所以只能在取到项目之后再过滤。
"""

import unittest
from types import SimpleNamespace

from app.services.project_hours_report_service import (
    PROJECT_STATUS_LABELS,
    _filter_projects,
    parse_project_statuses,
)


def _project(code: str, task_status: str):
    """按任务状态间接决定项目状态：全完成→已完成，有开始→进行中，否则未开始。"""
    task = SimpleNamespace(
        id=1, parent_id=None, status=task_status, time_slots=[], is_external_gate=False,
    )
    return SimpleNamespace(
        code=code, name=code, client_name=None, manager_name=None,
        start_date=None, tasks=[task],
    )


class ParseProjectStatusesTest(unittest.TestCase):
    def test_parses_comma_separated_values(self):
        self.assertEqual({"active", "completed"}, parse_project_statuses("active,completed"))

    def test_trims_and_drops_unknown_values(self):
        self.assertEqual({"active"}, parse_project_statuses(" active , bogus "))

    def test_empty_means_no_filter(self):
        self.assertEqual(set(), parse_project_statuses(None))
        self.assertEqual(set(), parse_project_statuses(""))

    def test_every_label_is_a_valid_filter_value(self):
        for status in PROJECT_STATUS_LABELS:
            self.assertEqual({status}, parse_project_statuses(status))


class FilterProjectsByStatusTest(unittest.TestCase):
    def setUp(self):
        self.projects = [
            _project("P-PENDING", "pending"),
            _project("P-ACTIVE", "running"),
            _project("P-DONE", "completed"),
        ]

    def _codes(self, statuses):
        return sorted(
            project.code
            for project in _filter_projects(self.projects, None, None, None, statuses)
        )

    def test_no_status_returns_everything(self):
        self.assertEqual(["P-ACTIVE", "P-DONE", "P-PENDING"], self._codes(None))
        self.assertEqual(["P-ACTIVE", "P-DONE", "P-PENDING"], self._codes(set()))

    def test_single_status(self):
        self.assertEqual(["P-DONE"], self._codes({"completed"}))

    def test_multiple_statuses(self):
        self.assertEqual(["P-ACTIVE", "P-DONE"], self._codes({"active", "completed"}))

    def test_status_combines_with_keyword(self):
        kept = _filter_projects(self.projects, None, None, "P-DONE", {"active"})

        self.assertEqual([], kept)


if __name__ == "__main__":
    unittest.main()

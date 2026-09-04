import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services.schedule_deadline_recommendation_job_service import (
    enqueue_deadline_recommendation,
)


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.filtered_ids: list[int] = []

    def filter(self, criterion):
        # Project.id.in_(candidate_ids) —— 只需要取回被问到的那批 ID。
        self.filtered_ids = list(criterion.right.value)
        return self

    def all(self):
        return [row for row in self._rows if row.id in self.filtered_ids]


class FakeSession:
    def __init__(self, rows):
        self.query_obj = FakeQuery(rows)
        self.added = []

    def query(self, _model):
        return self.query_obj

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


class DeadlineRecommendationCandidatesTest(unittest.TestCase):
    """候选项目必须覆盖被顶到结题日的项目，哪怕它不占用任何仪器。

    线上真实案例：检测任务 26083105 插队后，被顶破结题日的是另一个项目的
    "报告撰写"——纯人工任务，不出现在仪器占用清单里。候选名单只按仪器占用取，
    该项目整个进不了搜索范围，于是 1237 次组合试探全部失败，用户等满两分钟
    只拿到一张空白的调整方案表。
    """

    def setUp(self):
        self.current = SimpleNamespace(
            id=109, code="26083105", name="元素杂质检测",
            end_date=datetime(2026, 9, 30, 23, 59, 59),
        )
        self.instrument_user = SimpleNamespace(
            id=44, code="XM2026224", name="方法开发验证及检测",
            end_date=datetime(2026, 9, 15, 23, 59, 59),
        )
        self.report_only = SimpleNamespace(
            id=47, code="XM2026199", name="原料药元素杂质研究",
            end_date=datetime(2026, 9, 4, 23, 59, 59),
        )
        self.db = FakeSession([self.current, self.instrument_user, self.report_only])

    def enqueue(self, tasks):
        failure = {"occupancy": [{"project_id": 44}]}
        with patch(
            "app.services.schedule_deadline_recommendation_job_service.plan_fingerprint",
            return_value="fingerprint",
        ):
            enqueue_deadline_recommendation(
                self.db, self.current, tasks, self.current.end_date,
                datetime(2026, 9, 2), datetime(2026, 12, 1),
                {}, failure, {"mode": "insert"},
            )
        return self.db.added[0].payload

    def test_includes_project_of_a_replanned_task_that_uses_no_instrument(self):
        tasks = [
            SimpleNamespace(id=630, project_id=109),
            SimpleNamespace(id=246, project_id=44),
            SimpleNamespace(id=244, project_id=47),   # 报告撰写，不占仪器
        ]

        payload = self.enqueue(tasks)

        self.assertEqual([44, 47, 109], payload["project_ids"])
        self.assertIn("47", payload["original_deadlines"])
        self.assertEqual("XM2026199 · 原料药元素杂质研究", payload["project_labels"]["47"])

    def test_keeps_instrument_occupancy_projects(self):
        payload = self.enqueue([SimpleNamespace(id=630, project_id=109)])

        self.assertEqual([44, 109], payload["project_ids"])


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime
from types import SimpleNamespace

from app.services.project_health_scoring import calculate_health_score


class ProjectHealthScoringTest(unittest.TestCase):
    def _task(self, name, status="pending", hours=4):
        return SimpleNamespace(
            id=name,
            name=name,
            status=status,
            est_duration_hours=hours,
            additional_planned_minutes=0,
            executed_minutes=0,
            is_external_gate=False,
            gate_status="not_submitted",
        )

    def test_due_date_with_open_task_cannot_be_green(self):
        task = self._task("报告撰写")
        result = calculate_health_score(
            [task], {task.id: []}, "at_risk", datetime(2026, 9, 10, 17),
            datetime(2026, 9, 10, 23), datetime(2026, 9, 10), {task.id},
            {task.id: 4}, "not_scheduled",
        )
        self.assertIn(result.level, {"yellow", "red"})
        self.assertIn("结题日仍有未完成任务", result.reasons)

    def test_predicted_end_after_due_date_is_red(self):
        task = self._task("方法验证")
        result = calculate_health_score(
            [task], {task.id: []}, "overdue", datetime(2026, 9, 12),
            datetime(2026, 9, 10), datetime(2026, 9, 8), {task.id},
            {task.id: 4}, "scheduled",
        )
        self.assertEqual("red", result.level)
        self.assertIn("预测完工日晚于结题日期", result.reasons)

    def test_remaining_hours_are_reflected_in_progress_factor(self):
        long_task = self._task("长任务", hours=40)
        short_task = self._task("短任务", hours=1, status="done")
        result = calculate_health_score(
            [long_task, short_task], {long_task.id: [], short_task.id: []}, "on_time", None,
            None, datetime(2026, 9, 1), {long_task.id}, {long_task.id: 40}, "scheduled",
        )
        progress = next(item for item in result.factors if item.key == "progress")
        self.assertLess(progress.score, progress.max_score)


if __name__ == "__main__":
    unittest.main()

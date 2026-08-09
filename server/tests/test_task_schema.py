import unittest

from pydantic import ValidationError

from app.schemas.schemas import TaskOut


class TaskOutSchemaTest(unittest.TestCase):
    def _task_data(self) -> dict:
        return {
            "id": 1,
            "project_id": 2,
            "name": "检测任务",
            "task_type": "test",
            "requires_instrument": True,
            "requires_human": True,
            "est_duration_hours": 8,
            "switchover_hours": 0,
            "status": "pending",
            "earliest_start": None,
            "latest_due": None,
            "priority_weight": 1,
        }

    def test_project_id_is_required_integer(self):
        task = TaskOut.model_validate(self._task_data())
        self.assertEqual(task.project_id, 2)

        missing_project = self._task_data()
        missing_project.pop("project_id")
        with self.assertRaises(ValidationError):
            TaskOut.model_validate(missing_project)

        null_project = {**self._task_data(), "project_id": None}
        with self.assertRaises(ValidationError):
            TaskOut.model_validate(null_project)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.models import Task
from app.services.task_dependency_service import (
    create_continuous_successor,
    is_valid_continuous_successor,
)


class TaskDependencyServiceTest(unittest.TestCase):
    def test_continuous_successor_requires_same_parent_group(self):
        method = Task(id=1, project_id=10, parent_id=100, task_type="FFKF_001")
        scheme = Task(id=2, project_id=10, parent_id=200, task_type="QCFA_001")

        self.assertFalse(is_valid_continuous_successor(method, scheme))
        with self.assertRaisesRegex(ValueError, "同一顶级任务分组"):
            create_continuous_successor(method, scheme)

    def test_valid_method_scheme_pair_creates_continuous_dependency(self):
        method = Task(id=1, project_id=10, parent_id=100, task_type="FFKF_001")
        scheme = Task(id=2, project_id=10, parent_id=100, task_type="QCFA_001")

        dependency = create_continuous_successor(method, scheme)

        self.assertEqual("continuous_successor", dependency.dependency_type)
        self.assertEqual(1, dependency.predecessor_id)
        self.assertEqual(2, dependency.task_id)


if __name__ == "__main__":
    unittest.main()

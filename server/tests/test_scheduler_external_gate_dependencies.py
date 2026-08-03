import unittest
from types import SimpleNamespace

from app.services.scheduler_helpers import build_dependencies


class SchedulerExternalGateDependencyTest(unittest.TestCase):
    def test_external_gate_bridges_task_precedence(self):
        scheme = SimpleNamespace(id=1, is_external_gate=False, predecessors=[])
        gate = SimpleNamespace(
            id=2,
            is_external_gate=True,
            predecessors=[SimpleNamespace(predecessor=scheme)],
        )
        validation = SimpleNamespace(
            id=3,
            predecessors=[SimpleNamespace(predecessor=gate)],
        )

        dependencies = build_dependencies([scheme, validation])

        self.assertEqual([(3, 1)], dependencies)


if __name__ == "__main__":
    unittest.main()

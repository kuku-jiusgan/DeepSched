import unittest
from unittest.mock import patch

from app.services.schedule_conflict_service import ScheduleConflictError
from app.services.schedule_replan_validation_service import ensure_replan_consistent


class ScheduleReplanValidationServiceTest(unittest.TestCase):
    def test_runs_all_schedule_validations_with_correct_scope(self):
        with (
            patch(
                "app.services.schedule_replan_validation_service.ensure_no_instrument_conflicts"
            ) as instrument_check,
            patch(
                "app.services.schedule_replan_validation_service.ensure_no_human_conflicts"
            ) as human_check,
            patch(
                "app.services.schedule_replan_validation_service.ensure_no_dependency_conflicts"
            ) as dependency_check,
            patch(
                "app.services.schedule_replan_validation_service.stale_bridge_reservation_ids",
                return_value=[],
            ) as bridge_check,
        ):
            ensure_replan_consistent(
                object(),
                "run-1",
                [(2, 1)],
                [(4, 3)],
            )

        instrument_check.assert_called_once_with(unittest.mock.ANY, "run-1")
        human_check.assert_called_once_with(unittest.mock.ANY, "run-1")
        self.assertEqual(
            [
                unittest.mock.call(unittest.mock.ANY, [(2, 1)], "run-1"),
                unittest.mock.call(
                    unittest.mock.ANY,
                    [(4, 3)],
                    "run-1",
                    task_slots_from_run_only=True,
                ),
            ],
            dependency_check.call_args_list,
        )
        bridge_check.assert_called_once_with(unittest.mock.ANY, "run-1")

    def test_rejects_stale_bridge_reservation(self):
        with (
            patch(
                "app.services.schedule_replan_validation_service.ensure_no_instrument_conflicts"
            ),
            patch(
                "app.services.schedule_replan_validation_service.ensure_no_human_conflicts"
            ),
            patch(
                "app.services.schedule_replan_validation_service.ensure_no_dependency_conflicts"
            ),
            patch(
                "app.services.schedule_replan_validation_service.stale_bridge_reservation_ids",
                return_value=[7],
            ),
        ):
            with self.assertRaisesRegex(
                ScheduleConflictError,
                "仪器桥接占用记录与当前排程不一致",
            ):
                ensure_replan_consistent(object(), "run-1", [], [])


if __name__ == "__main__":
    unittest.main()

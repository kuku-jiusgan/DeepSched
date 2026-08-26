from datetime import datetime, timedelta

from app.services.task_pause_solver_service import _solver_horizon_end


def test_pause_solver_horizon_covers_remaining_workload() -> None:
    queue_end = datetime(2026, 8, 26, 18, 0)

    horizon_end = _solver_horizon_end(queue_end, {1: 35 * 60, 2: 4 * 60})

    assert horizon_end == queue_end + timedelta(days=7)


def test_pause_solver_horizon_keeps_minimum_buffer() -> None:
    queue_end = datetime(2026, 8, 26, 18, 0)

    horizon_end = _solver_horizon_end(queue_end, {1: 30})

    assert horizon_end == queue_end + timedelta(days=3)

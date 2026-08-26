from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4


SOLVER_LOG_DIR = Path(__file__).resolve().parents[3] / "solver_logs"


class SolverTrace:
    def __init__(self, project_id: int, task_count: int, mode: str, time_limit: float):
        SOLVER_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path = SOLVER_LOG_DIR / (
            f"cp_sat_project_{project_id}_{timestamp}_{uuid4().hex[:8]}.log"
        )
        self._file = self.path.open("w", encoding="utf-8")
        self._started_at = perf_counter()
        self._write(
            f"CP-SAT trace started_at={datetime.now().isoformat()} "
            f"project_id={project_id} task_count={task_count} mode={mode} "
            f"time_limit_seconds={time_limit}\n"
        )

    def write(self, message: str) -> None:
        self._write(message)

    def write_model(self, model) -> None:
        self._write("\n=== MODEL STATS ===\n")
        self._write(model.ModelStats())
        self._write("\n=== SEARCH PROGRESS ===\n")

    def finish(self, solver, status_name: str) -> int:
        elapsed_ms = round((perf_counter() - self._started_at) * 1000)
        self._write("\n=== FINAL RESPONSE ===\n")
        self._write(solver.ResponseStats())
        self._write(f"\nstatus={status_name} elapsed_ms={elapsed_ms}\n")
        self._file.close()
        return elapsed_ms

    def _write(self, message: str) -> None:
        self._file.write(message if message.endswith("\n") else f"{message}\n")
        self._file.flush()

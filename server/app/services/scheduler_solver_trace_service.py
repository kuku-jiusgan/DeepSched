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

    def write_fixed_slot_registry(self, db, slots) -> None:
        """把这次求解看到的固定时间槽登记进日志。

        原先是逐槽懒加载：每条槽要各查一次所属项目、负责人、仪器，再顺着父任务
        一路上溯。实测一次排程光这份日志就发 27 条 SQL，和整个装载阶段（29 条）
        几乎一样多——纯粹为了写一份排查用的日志。现在一次把要用的名字全取回来，
        条数不随槽数增长。
        """
        self._write("\n=== FIXED SLOT REGISTRY ===\n")
        names = _resolve_slot_names(db, slots)
        for slot in slots:
            info = names.get(slot.id, {})
            self._write(
                f"fixed_slot_{slot.id}: project={info.get('project')} "
                f"top_task={info.get('top_task')} task={info.get('task')} "
                f"assignee={info.get('assignee')} "
                f"instrument={info.get('instrument')} "
                f"plan=({slot.plan_start},{slot.plan_end}) "
                f"actual=({slot.actual_start},{slot.actual_end}) status={slot.status} tier={slot.tier}"
            )

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


def _resolve_slot_names(db, slots) -> dict:
    """一次取齐日志要用的名字：项目号、任务名、顶层任务名、负责人、仪器。

    查询条数固定（5 条），不随时间槽数量增长。
    """
    from app.models import Instrument, Project, Task, User

    if db is None or not slots:
        return {}
    task_ids = {slot.task_id for slot in slots if slot.task_id is not None}
    instrument_ids = {
        slot.instrument_id for slot in slots if slot.instrument_id is not None
    }
    if not task_ids:
        return {}
    tasks = {
        task.id: task for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
    }
    project_ids = {task.project_id for task in tasks.values() if task.project_id}
    # 顶层任务名要顺着 parent 上溯，把这些项目的整棵树一次取回来在内存里走。
    tree = {
        row.id: row for row in db.query(
            Task.id, Task.parent_id, Task.name,
        ).filter(Task.project_id.in_(project_ids)).all()
    } if project_ids else {}
    projects = {
        row.id: row.code for row in db.query(Project.id, Project.code).filter(
            Project.id.in_(project_ids),
        ).all()
    } if project_ids else {}
    assignee_ids = {task.assignee_id for task in tasks.values() if task.assignee_id}
    users = {
        row.id: row.display_name for row in db.query(User.id, User.display_name).filter(
            User.id.in_(assignee_ids),
        ).all()
    } if assignee_ids else {}
    instruments = {
        row.id: row.name for row in db.query(Instrument.id, Instrument.name).filter(
            Instrument.id.in_(instrument_ids),
        ).all()
    } if instrument_ids else {}

    def top_name(task_id):
        seen = set()
        current = tree.get(task_id)
        while current is not None and current.parent_id and current.id not in seen:
            seen.add(current.id)
            current = tree.get(current.parent_id)
        return current.name if current is not None else None

    result = {}
    for slot in slots:
        task = tasks.get(slot.task_id)
        result[slot.id] = {
            "project": projects.get(task.project_id) if task else None,
            "top_task": top_name(slot.task_id),
            "task": task.name if task else None,
            "assignee": users.get(task.assignee_id) if task else None,
            "instrument": instruments.get(slot.instrument_id),
        }
    return result

"""按工作日历重算 task.executed_minutes。

历史值是按墙钟差值累加的（见修复前的 task_pause_service._elapsed_execution_minutes），
跨夜和跨周末的执行会被严重高估：周五 18:00 到周一 10:00 记成 3840 分钟，
实际有效工时只有 210 分钟。这个字段被 planned_task_minutes 减去决定重排时长，
也用来判断任务能否标记完成，高估会让求解器少排工时、让任务过早显示可完成。

默认只打印对比，加 --apply 才写库。
"""

import argparse
import sys

from app.core.database import SessionLocal
from app.models import Task, TaskExecutionSegment
from app.services.schedule_working_time_service import working_hours_between
from app.services.task_progress_service import planned_task_minutes


def recomputed_minutes(db, task: Task) -> int:
    """已结束执行区间的有效工时合计，上限是任务计划工时。

    进行中的区间（ended_at 为空）不计入：它的工时会在下次暂停时由
    _elapsed_execution_minutes 累加，提前算进来会重复计数。
    """
    minutes = sum(
        working_hours_between(db, segment.started_at, segment.ended_at, segment.instrument_id) * 60
        for segment in task.execution_segments
        if segment.started_at and segment.ended_at
    )
    return min(planned_task_minutes(task), max(0, int(minutes)))


def affected_tasks(db) -> list[Task]:
    task_ids = {
        row[0] for row in db.query(TaskExecutionSegment.task_id)
        .filter(TaskExecutionSegment.ended_at.isnot(None)).distinct()
    }
    task_ids |= {row[0] for row in db.query(Task.id).filter(Task.executed_minutes > 0)}
    if not task_ids:
        return []
    return db.query(Task).filter(Task.id.in_(task_ids)).order_by(Task.id).all()


DONE_STATUSES = {"done", "completed"}


def build_rows(db, tasks: list[Task]) -> list[dict]:
    rows = []
    for task in tasks:
        old = int(task.executed_minutes or 0)
        new = recomputed_minutes(db, task)
        if old == new:
            continue
        rows.append({
            "task": task,
            "planned": planned_task_minutes(task),
            "old": old,
            "new": new,
        })
    return rows


def in_scope(row: dict, scope: str) -> bool:
    """限定回填范围。

    inflated：只修墙钟高估，即未完成任务里新值更小的那些。已完成任务没有
    消费方（不再重排、不再判定可完成），调高未完成任务的已执行工时则会让
    求解器少排工时，都需要单独确认，不放进默认范围。
    """
    if scope == "all":
        return True
    if row["task"].status in DONE_STATUSES:
        return False
    if scope == "active":
        return True
    return row["new"] < row["old"]


def print_rows(rows: list[dict]) -> None:
    if not rows:
        print("没有需要回填的任务。")
        return
    header = f"{'ID':>5}  {'状态':<10} {'任务':<14} {'计划':>7} {'原值':>7} {'新值':>7} {'差值':>8}"
    print(header)
    print("-" * len(header))
    for row in rows:
        task = row["task"]
        print(
            f"{task.id:>5}  {task.status or '-':<10} {(task.name or '-')[:14]:<14} "
            f"{row['planned']:>7} {row['old']:>7} {row['new']:>7} {row['new'] - row['old']:>+8}"
        )
    print(f"\n共 {len(rows)} 个任务需要回填。")


def main() -> int:
    parser = argparse.ArgumentParser(description="按工作日历重算 task.executed_minutes")
    parser.add_argument("--apply", action="store_true", help="写库；不加则只打印对比")
    parser.add_argument(
        "--scope", choices=("inflated", "active", "all"), default="inflated",
        help="inflated：只修未完成任务里被墙钟高估的值（默认）；"
             "active：全部未完成任务；all：含已完成任务",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = [
            row for row in build_rows(db, affected_tasks(db))
            if in_scope(row, args.scope)
        ]
        print_rows(rows)
        if not rows or not args.apply:
            if rows:
                print("这是预演，未写库。确认无误后加 --apply 执行。")
            return 0
        for row in rows:
            row["task"].executed_minutes = row["new"]
        db.commit()
        print("已写库。")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

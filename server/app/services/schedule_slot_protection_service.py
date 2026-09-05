from __future__ import annotations

from datetime import datetime

from app.models import TimeSlot


def tasks_with_immovable_slot(
    db, task_ids, at: datetime | None = None,
) -> set[int]:
    """一次查出这批任务里哪些有"不可移动"的时间槽。

    判定条件有两种：已经开工还没结束（有实际开始、无实际结束），或者被冻结且
    计划尚未走完。逐个任务问一遍是天然的 N+1——插单候选筛选那个循环里每个
    任务发一条，实测一次排程光这一处就有 43 条。

    注意时间基准：逐个问的时候每次各取一次 datetime.now()，批量则整批共用一个
    基准。差别在毫秒级，而且整批用同一个"现在"本来就更自洽——同一次筛选里
    不该出现前一个任务和后一个任务站在不同时刻上判断。
    """
    task_ids = {int(task_id) for task_id in task_ids}
    if not task_ids:
        return set()
    boundary = at or datetime.now()
    rows = db.query(TimeSlot.task_id).filter(
        TimeSlot.task_id.in_(task_ids),
        TimeSlot.lifecycle_status == "active",
        (
            (TimeSlot.actual_start.isnot(None) & TimeSlot.actual_end.is_(None))
            | (
                (TimeSlot.tier == "frozen")
                & TimeSlot.actual_start.is_(None)
                & (TimeSlot.plan_end > boundary)
            )
        ),
    ).distinct().all()
    return {task_id for (task_id,) in rows}


def task_has_immovable_slot(db, task_id: int, at: datetime | None = None) -> bool:
    return bool(tasks_with_immovable_slot(db, [task_id], at))

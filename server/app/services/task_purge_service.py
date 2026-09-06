"""物理删除任务时要一并清掉的那些引用。

指向 task 和 time_slot 的外键有十来个，删除规则清一色是 NO ACTION——漏清一张表，
删除就报 IntegrityError 1451。线上踩过三种：time_slot、task_night_run、
instrument_bridge_reservation，分别在删任务和删项目两条路上。

删任务和删项目讲的是同一件事：把这些任务连同它们在时间轴上留下的一切痕迹抹掉。
所以这套顺序只能有一份，两条路都从这里走，免得一边补了另一边还漏着。
"""

from __future__ import annotations

from app.models import (
    InstrumentBridgeReservation,
    Task,
    TaskCapabilityRequirement,
    TaskDependency,
    TaskExecutionSegment,
    TaskNightRun,
    TimeSlot,
)


def purge_task_trees(db, task_ids: set[int]) -> set[int]:
    """删掉这些任务及其全部引用，返回受影响的仪器 id。

    时间槽是**物理删除**，不是作废。任务本身都要没了，留着指向它的槽既没有意义，
    也会被外键直接拦下：非系统管理员删一个已排程的任务，此前走的正是"只作废槽"
    那条路，结果任务删不掉，报的就是 time_slot 那条外键。
    """
    if not task_ids:
        return set()
    instrument_ids = {
        instrument_id
        for instrument_id, in db.query(TimeSlot.instrument_id).filter(
            TimeSlot.task_id.in_(task_ids), TimeSlot.instrument_id.isnot(None),
        ).distinct().all()
    }
    db.query(TaskDependency).filter(
        (TaskDependency.predecessor_id.in_(task_ids))
        | (TaskDependency.task_id.in_(task_ids))
    ).delete(synchronize_session=False)
    db.query(TaskCapabilityRequirement).filter(
        TaskCapabilityRequirement.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    db.query(TaskNightRun).filter(
        TaskNightRun.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    db.query(TaskExecutionSegment).filter(
        TaskExecutionSegment.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    # 桥接预留有三个字段指向任务，任意一个落在删除集合里，这条预留就没有意义了。
    db.query(InstrumentBridgeReservation).filter(
        (InstrumentBridgeReservation.task_id.in_(task_ids))
        | (InstrumentBridgeReservation.previous_task_id.in_(task_ids))
        | (InstrumentBridgeReservation.following_task_id.in_(task_ids))
    ).delete(synchronize_session=False)
    db.query(TimeSlot).filter(
        TimeSlot.task_id.in_(task_ids)
    ).delete(synchronize_session=False)
    # task.parent_id 是自引用外键，父子行的删除顺序由数据库决定，先父后子就报
    # 1451。删除在即，先把父子关系解开，顺序问题也就不存在了。
    db.query(Task).filter(Task.id.in_(task_ids)).update(
        {Task.parent_id: None}, synchronize_session=False,
    )
    db.query(Task).filter(Task.id.in_(task_ids)).delete(synchronize_session=False)
    db.flush()
    return instrument_ids

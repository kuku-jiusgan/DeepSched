"""求解器面对的那道题。

求解器本该是纯函数：世界进去，计划出来。当前不是——`_execute_replan` 先把可移动
时间槽删掉、把任务状态改回待排、flush，然后求解器再回头查库才知道哪些资源被占。
假设的最小单元于是变成"一个事务"而不是"一个值"，由此派生出探测要包 savepoint、
要防止真提交、要全局互斥、并行探测撞行锁等一连串绕行。

这个模块是把它掰回来的落点：**一次装载，之后纯内存**。

行业里这是标准做法（OptaPlanner 的 PlanningSolution、SAP APO 的 liveCache、
Preactor/Quintiq 都是同一形状），OR-Tools 自己就是纯函数，耦合完全是应用层引入的。
本项目的规划世界只有几千行、约 5 MB，装载是廉价的。

迁移纪律：**一次搬一块，每搬一块都要求求解模型序列化后逐字节不变**
（tools/model_equivalence.py）。只改取数方式，不改喂给求解器的那道题。

已经搬进来的：时间原点与求解视界、排程规则、工作日历、仪器。
还在库里直接取的：任务实体、固定时间槽、桥接预留、签批门上下界。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.services.calendar_service import ensure_calendar_range
from app.services.schedule_epoch_service import current_epoch
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import load_calendar_days, time_horizon


@dataclass(frozen=True)
class SolverRule:
    """一条排程规则的取值。

    原先直接把 ScheduleRule 这个 ORM 实体传进求解流程。实体绑在会话上，任何一次
    属性访问都可能触发隐式 SQL，也没法跨进程传递。求解只用到 code / params /
    is_enabled 三样，取成值即可。
    """

    code: str
    is_enabled: bool
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityView:
    tag_name: str
    tag_value: str


@dataclass(frozen=True)
class MaintenanceWindowView:
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class FaultView:
    status: str
    reported_at: datetime | None
    estimated_resolved_at: datetime | None
    resolved_at: datetime | None


@dataclass(frozen=True)
class InstrumentView:
    """一台仪器在求解眼里的样子。

    字段名与 ORM 实体保持一致，下游的能力匹配、维护窗口、工作时段计算都不用改。
    区别在于它是值：不绑会话、不会因为一次属性访问偷偷发 SQL，也能跨进程传递。
    """

    id: int
    code: str
    name: str
    status: str
    effective_work_start: str
    effective_work_end: str
    capabilities: tuple[CapabilityView, ...] = ()
    maintenance_windows: tuple[MaintenanceWindowView, ...] = ()
    faults: tuple[FaultView, ...] = ()


@dataclass(frozen=True)
class ProjectView:
    id: int
    code: str | None
    name: str | None
    priority: int | None
    start_date: datetime | None
    end_date: datetime | None
    # 诊断路径会从任务反向拿项目的全量任务集合。求解主链路用不到，这里恒为空元组，
    # 失败分支会把任务重新取成 ORM 实体再走诊断。
    tasks: tuple = ()


@dataclass(frozen=True)
class MilestoneView:
    id: int
    due_date: datetime | None


@dataclass(frozen=True)
class CapabilityRequirementView:
    tag_name: str
    tag_value: str


@dataclass(frozen=True)
class DependencyView:
    """一条依赖边。字段名与 TaskDependency 对齐，下游读的是 dependency.predecessor。"""

    task_id: int
    predecessor_id: int
    predecessor: "TaskView | None" = None


@dataclass(frozen=True, eq=False)
class TaskView:
    """一个任务在求解眼里的样子。

    字段名与 ORM 实体保持一致，下游的建模、目标函数、指令集一行不用改。区别在于
    它是值：不绑会话、属性访问不会偷偷发 SQL、能跨进程传。

    eq=False 是必要的：predecessors 里的视图会指回来形成环，默认的逐字段比较会
    无限递归。

    ⚠️ 字段一个都不能少。scheduler_task_duration 里有一处
    `hasattr(task, "executed_minutes")` 分支——漏掉这个字段不会报错，会静默跌进
    另一套时长算法，而那个值直接进 NewIntVar 的边界。靠模型字节对照兜底。
    """

    id: int
    name: str | None
    status: str | None
    task_type: str | None
    project_id: int | None
    milestone_id: int | None
    parent_id: int | None
    assignee_id: int | None
    requires_instrument: bool
    requires_human: bool
    est_duration_hours: float | None
    switchover_hours: float | None
    allow_split: bool
    # 必须是 list：_parse_instrument_ids 只认 list / str / 标量，元组会掉进
    # int(raw_ids) 那条分支报错。
    instrument_ids: list
    priority_weight: int | None
    created_at: datetime | None
    executed_minutes: int
    additional_planned_minutes: int
    latest_due: datetime | None
    is_external_gate: bool
    project: ProjectView | None = None
    milestone: MilestoneView | None = None
    capability_requirements: tuple[CapabilityRequirementView, ...] = ()
    predecessors: tuple[DependencyView, ...] = ()
    # 求解主链路用不到；诊断路径会用，但那条路会把任务重新取成 ORM 实体。
    time_slots: tuple = ()
    execution_segments: tuple = ()


@dataclass(frozen=True)
class SlotTaskView:
    """固定时间槽背后的任务，只带产能约束用得到的几项。"""

    id: int
    project_id: int | None
    requires_human: bool
    assignee_id: int | None


@dataclass(frozen=True)
class TimeSlotView:
    """一个已占用的时间槽。字段名与 TimeSlot 对齐。"""

    id: int
    task_id: int | None
    instrument_id: int | None
    plan_start: datetime | None
    plan_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    status: str | None
    tier: str | None
    lifecycle_status: str | None
    task: SlotTaskView | None = None


@dataclass(frozen=True)
class BridgeReservationView:
    """桥接预留。它没有执行状态，占用区间就是计划区间。

    必须带 is_bridge_reservation 标记：_fixed_slot_range 原先靠
    isinstance(InstrumentBridgeReservation) 分流，值对象过不了那道判断，会掉进
    时间槽分支去读 slot.status 而抛 AttributeError。此前快照适配器就踩过这个坑。
    """

    id: int
    task_id: int | None
    instrument_id: int | None
    plan_start: datetime | None
    plan_end: datetime | None
    is_bridge_reservation: bool = True


@dataclass(frozen=True)
class PlanningProblem:
    """一次求解的输入。构造完成后不再依赖数据库会话。"""

    now: datetime
    horizon_start: datetime
    horizon_end: datetime
    total_units: int
    rules: dict[str, SolverRule]
    calendar_days: dict
    instruments: tuple[InstrumentView, ...]
    tasks: tuple[TaskView, ...] = ()
    # 装载这一刻的排程版本号。写回时用它做条件更新，确认这中间没人动过排程。
    epoch: int = 0

    def __getitem__(self, code: str) -> SolverRule:
        """让它能直接顶替原来那个 constraints 字典。"""
        return self.rules[code]

    def __contains__(self, code: str) -> bool:
        return code in self.rules


def build_planning_problem(
    db,
    *,
    now: datetime,
    planning_start_at: datetime | None = None,
    planning_end_at: datetime | None = None,
) -> PlanningProblem:
    """从数据库装载一次求解所需的世界。

    这里是允许碰数据库的地方——也是唯一允许的地方。补齐工作日历这个写操作也放在
    这里：它是装载的一部分（视界内缺哪天就补哪天），放在建模过程中间会让"求解不
    写库"这句话不成立。
    """
    horizon_start, horizon_end, total_units = time_horizon(
        planning_start_at, planning_end_at, now,
    )
    ensure_calendar_range(db, horizon_start.date(), horizon_end.date())
    return PlanningProblem(
        calendar_days=load_calendar_days(db, horizon_start, horizon_end),
        instruments=_load_instrument_views(db),
        epoch=current_epoch(db),
        now=now,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        total_units=total_units,
        rules={
            code: SolverRule(
                code=code,
                is_enabled=bool(rule.is_enabled),
                params=dict(rule.params or {}),
            )
            for code, rule in get_solver_constraints(db).items()
        },
    )


def _load_instrument_views(db) -> tuple[InstrumentView, ...]:
    """装载参与排程的仪器，并转成值。

    排序不能省：CP-SAT 的变量和约束索引按创建顺序分配，仪器顺序决定了候选顺序、
    产能约束顺序和目标项顺序，不定序的话同一道题两次建出的模型就不同。
    """
    from app.services.scheduler_data import load_instruments

    return tuple(
        InstrumentView(
            id=instrument.id,
            code=instrument.code,
            name=instrument.name,
            status=instrument.status,
            effective_work_start=instrument.effective_work_start,
            effective_work_end=instrument.effective_work_end,
            capabilities=tuple(
                CapabilityView(capability.tag_name, capability.tag_value)
                for capability in sorted(
                    instrument.capabilities,
                    key=lambda item: (item.tag_name, item.tag_value, item.id),
                )
            ),
            maintenance_windows=tuple(
                MaintenanceWindowView(window.start_time, window.end_time)
                for window in sorted(
                    instrument.maintenance_windows,
                    key=lambda item: (item.start_time, item.end_time, item.id),
                )
            ),
            faults=tuple(
                FaultView(
                    status=fault.status,
                    reported_at=fault.reported_at,
                    estimated_resolved_at=fault.estimated_resolved_at,
                    resolved_at=fault.resolved_at,
                )
                for fault in sorted(
                    instrument.faults or [],
                    key=lambda item: (item.reported_at or datetime.min, item.id),
                )
            ),
        )
        for instrument in load_instruments(db)
    )


def build_task_views(orm_tasks) -> tuple[TaskView, ...]:
    """把 ORM 任务转成值对象，保持传入顺序。

    分两遍：先给每个任务（含只作为前置出现的那些）建好视图，再回填依赖边。
    依赖会穿过签批门递归展开（见 scheduler_helpers._effective_predecessor_ids），
    所以前置任务即使不在求解集合里也必须有视图，否则展开会断在门上。
    """
    views: dict[int, TaskView] = {}
    dependencies: dict[int, list] = {}

    def register(task) -> TaskView:
        existing = views.get(task.id)
        if existing is not None:
            return existing
        view = _task_view(task)
        views[task.id] = view
        dependencies[task.id] = list(getattr(task, "predecessors", None) or [])
        for dependency in dependencies[task.id]:
            predecessor = getattr(dependency, "predecessor", None)
            if predecessor is not None:
                register(predecessor)
        return view

    ordered = [register(task) for task in orm_tasks]
    for task_id, rows in dependencies.items():
        edges = tuple(
            DependencyView(
                task_id=task_id,
                predecessor_id=dependency.predecessor_id,
                predecessor=views.get(dependency.predecessor_id),
            )
            # 定序：依赖边的顺序会决定约束的创建顺序。
            for dependency in sorted(rows, key=lambda item: item.predecessor_id)
        )
        object.__setattr__(views[task_id], "predecessors", edges)
    return tuple(ordered)


def _task_view(task) -> TaskView:
    project = getattr(task, "project", None)
    milestone = getattr(task, "milestone", None)
    raw_instrument_ids = getattr(task, "instrument_ids", None)
    return TaskView(
        id=task.id,
        name=task.name,
        status=task.status,
        task_type=getattr(task, "task_type", None),
        project_id=task.project_id,
        milestone_id=getattr(task, "milestone_id", None),
        parent_id=getattr(task, "parent_id", None),
        assignee_id=getattr(task, "assignee_id", None),
        requires_instrument=bool(getattr(task, "requires_instrument", False)),
        requires_human=bool(getattr(task, "requires_human", False)),
        est_duration_hours=getattr(task, "est_duration_hours", None),
        switchover_hours=getattr(task, "switchover_hours", None),
        allow_split=bool(getattr(task, "allow_split", False)),
        instrument_ids=list(raw_instrument_ids) if isinstance(raw_instrument_ids, (list, tuple))
        else raw_instrument_ids,
        priority_weight=getattr(task, "priority_weight", None),
        created_at=getattr(task, "created_at", None),
        executed_minutes=int(getattr(task, "executed_minutes", 0) or 0),
        additional_planned_minutes=int(getattr(task, "additional_planned_minutes", 0) or 0),
        latest_due=getattr(task, "latest_due", None),
        is_external_gate=bool(getattr(task, "is_external_gate", False)),
        project=ProjectView(
            id=project.id, code=project.code, name=project.name,
            priority=project.priority,
            start_date=project.start_date, end_date=project.end_date,
        ) if project is not None else None,
        milestone=MilestoneView(
            id=milestone.id, due_date=milestone.due_date,
        ) if milestone is not None else None,
        capability_requirements=tuple(
            CapabilityRequirementView(item.tag_name, item.tag_value)
            for item in sorted(
                getattr(task, "capability_requirements", None) or [],
                key=lambda item: (item.tag_name, item.tag_value, item.id),
            )
        ),
    )


def to_slot_views(slots) -> list[TimeSlotView]:
    """把固定时间槽转成值对象，保持传入顺序（顺序决定约束的创建次序）。"""
    return [
        TimeSlotView(
            id=slot.id, task_id=slot.task_id, instrument_id=slot.instrument_id,
            plan_start=slot.plan_start, plan_end=slot.plan_end,
            actual_start=slot.actual_start, actual_end=slot.actual_end,
            status=slot.status, tier=slot.tier,
            lifecycle_status=getattr(slot, "lifecycle_status", None),
            task=SlotTaskView(
                id=slot.task.id, project_id=slot.task.project_id,
                requires_human=bool(getattr(slot.task, "requires_human", False)),
                assignee_id=getattr(slot.task, "assignee_id", None),
            ) if getattr(slot, "task", None) is not None else None,
        )
        for slot in slots
    ]


def to_bridge_views(reservations) -> list[BridgeReservationView]:
    return [
        BridgeReservationView(
            id=item.id, task_id=item.task_id, instrument_id=item.instrument_id,
            plan_start=item.plan_start, plan_end=item.plan_end,
        )
        for item in reservations
    ]

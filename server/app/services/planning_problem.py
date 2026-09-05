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
class PlanningProblem:
    """一次求解的输入。构造完成后不再依赖数据库会话。"""

    now: datetime
    horizon_start: datetime
    horizon_end: datetime
    total_units: int
    rules: dict[str, SolverRule]
    calendar_days: dict
    instruments: tuple[InstrumentView, ...]
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

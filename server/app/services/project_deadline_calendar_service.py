"""项目结题日期与工作日历的对齐。

结题日期是跟客户签的合同日期，落在周末或法定假日上没有意义；更实际的问题是它
腾不出任何工时——排程只在工作时段里落任务，把结题日从周六挪到周日，可用工时
一分钟都没多。线上就出过这种建议：原结题日 2026-09-12（周六），建议延到
09-13（周日）。

周末算不算工作时间由排程规则决定（include_weekends / include_holidays），所以
这里按规则加日历判断，而不是简单地跳过周六周日。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services.project_date_service import normalize_project_end
from app.services.schedule_rule_service import get_solver_constraints
from app.services.scheduler_helpers import is_allowed_calendar_day, load_calendar_days


SEARCH_DAYS = 60


def working_day_flags(db) -> tuple[bool, bool]:
    """(include_weekends, include_holidays)。工作时间规则整体关闭时一律按可用处理。"""
    rule = get_solver_constraints(db)["working_hours"]
    params = rule.params or {}
    return (
        bool(params.get("include_weekends", False)) or not rule.is_enabled,
        bool(params.get("include_holidays", False)) or not rule.is_enabled,
    )


def next_working_deadline(db, moment: datetime) -> datetime:
    """把一个预计完工时刻归一成可以直接填进项目结题日期的那一天。"""
    include_weekends, include_holidays = working_day_flags(db)
    calendar_days = load_calendar_days(db, moment, moment + timedelta(days=SEARCH_DAYS))
    for offset in range(SEARCH_DAYS + 1):
        day = moment + timedelta(days=offset)
        if is_allowed_calendar_day(
            day.date(), calendar_days, include_weekends, include_holidays,
        ):
            return normalize_project_end(day)
    raise ValueError(
        f"{moment:%Y-%m-%d} 起 {SEARCH_DAYS} 天内没有工作日，无法给出建议结题日期"
    )

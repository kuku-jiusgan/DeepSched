"""任务工时输入的归一化。

排程颗粒度是 30 分钟，求解器建模时会把计划工时和切换时间各自向上取整到
整数个时间单元。如果入口允许 1.7 小时这种值，求解器实际按 2.0 小时排，
而工时校验、缺口分析和界面上显示的都还是 1.7 小时，各处对不上；更糟的是
时长和切换时间是分两次取整的，1.2 小时 + 0.2 小时会被算成 2.0 小时，
合并后其实只要 1.5 小时。

在写入时就取整到 0.5 小时的整数倍，上面两个问题一起消失：取整后每个值都
正好是整数个时间单元，分两次取整和合并取整的结果相同。

只对人工填写的工时取整。标准计划模板和计划草稿里的工时是按项目预计工时
百分比分摊出来的，必须精确加总；向上取整会让任务合计超出项目预计工时，
反而触发 validate_project_estimated_hours 的校验失败。
"""

from __future__ import annotations

import math


# 与 scheduler_helpers.TIME_UNIT_MINUTES 对应，由测试锁死两者一致。
TIME_UNIT_HOURS = 0.5


def round_up_to_time_unit(hours: float | None) -> float | None:
    """把工时向上取整到排程颗粒度；None 原样返回，非正数归零。"""
    if hours is None:
        return None
    value = float(hours)
    if value <= 0:
        return 0.0
    return math.ceil(value / TIME_UNIT_HOURS) * TIME_UNIT_HOURS

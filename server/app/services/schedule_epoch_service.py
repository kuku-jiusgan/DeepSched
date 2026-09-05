"""排程状态的版本号，用于写回时的乐观锁。

要解决的问题是"检查与执行之间的时间差"：一份计划是针对装载那一刻的世界算出来的，
写回之前必须确认没人动过排程。原先这个判断写成 Python 里的一个 if——先算指纹、
比对、再写；检查通过和真正写下去之间隔着几毫秒，别人照样能在这条缝里提交。

这里把判断下推到数据库：比对和推进是同一条 UPDATE 的两半，条件不成立就是影响
0 行，没有缝可钻。

粒度是全局一个版本号，而不是每张表每行一个。理由有二：排程本来就是全局互斥的
一件事；原先的 plan_fingerprint 也是把全库时间槽一起算进指纹的，语义相同，只是
它要全表扫描，这里换成一个整数比较。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.domain.errors import DomainConflictError


class ScheduleStaleError(DomainConflictError):
    """装载世界之后排程被别人改过，这份计划不能再写回。"""


def current_epoch(db) -> int:
    """读当前版本号；单行不存在就建出来。

    自初始化是必要的：条件更新匹配不到行就会被当成"世界变了"而整批失败，而一个
    刚建好还没跑过迁移的库、或者测试里现建的内存库，本来就没有这一行。
    """
    row = db.execute(text("SELECT version FROM schedule_epoch WHERE id = 1")).first()
    if row is None:
        # updated_at 的默认值是 ORM 侧的，走原生 SQL 时不会生效，必须显式给。
        db.execute(
            text(
                "INSERT INTO schedule_epoch (id, version, updated_at)"
                " VALUES (1, 0, :now)"
            ),
            {"now": datetime.now()},
        )
        db.flush()
        return 0
    return int(row[0])


def claim(db, expected: int) -> None:
    """确认版本仍是 expected 并把它推进一格。不成立就抛错。

    比对写进 WHERE 里，所以"检查"和"占用"是同一个原子动作。影响 0 行有两种可能：
    版本已经被别人推进了，或者单行还不存在（迁移未跑）——两种都不该继续写下去。
    """
    current_epoch(db)  # 保证单行存在，否则匹配 0 行会被误判成"世界变了"
    result = db.execute(
        text(
            "UPDATE schedule_epoch SET version = version + 1, updated_at = :now"
            " WHERE id = 1 AND version = :expected"
        ),
        {"expected": expected, "now": datetime.now()},
    )
    if result.rowcount != 1:
        raise ScheduleStaleError(
            "排程数据在本次计算期间已被改动，请重新排程"
        )

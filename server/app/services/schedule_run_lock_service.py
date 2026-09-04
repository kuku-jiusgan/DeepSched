"""排程互斥：同一时刻只允许一次排程计算在跑。

一次排程要先删旧时间槽、把任务改回待排，再经过几十秒求解，最后写回新时间槽。
两次排程撞在一起时原先没有任何拦阻，全靠 MySQL 行锁被动排队——后来的那次一直
等到 innodb_lock_wait_timeout（默认 50 秒）才抛 1205，前端只能显示一句"失败"，
分不清是撞了车还是真的排不下。线上就这么出过事：后台在为一次排程失败搜索结题日
调整方案，用户重复点"保存并排程"，两次都等满 50 秒拿到 500。

改成先抢锁：抢不到就立刻告诉用户是谁在算、已经算了多久，请稍后重试——等待和
失败要当场分清楚，不能让人误以为方案真的排不下。

前提：后端跑在单个 uvicorn 进程里（start.sh 未传 --workers），用户请求和后台
worker 线程共用这一个进程，进程内互斥即可覆盖全部排程入口。若将来改成多进程或
多实例部署，这把锁必须换成 worker_lease 表上的租约，否则跨进程又会退回行锁等待。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from time import monotonic

from app.domain.errors import DomainConflictError

SCHEDULE_RUN = "排程计算"
DEADLINE_RECOMMENDATION = "排程调整方案计算"

# 可重入：后台方案搜索本身就是一次持锁的排程，它内部要反复调用求解器，不能
# 每探测一个候选日期就自己跟自己抢一次锁。同一线程内的嵌套直接放行。
_lock = threading.RLock()
_current_activity: str | None = None
_started_at: float = 0.0
_depth = 0


class ScheduleBusyError(DomainConflictError):
    """已有排程在跑，本次请求不排队、直接退回。"""


@contextmanager
def schedule_run_lock(activity: str):
    """排他地跑一次排程；已被别的线程占用时立刻抛 ScheduleBusyError。"""
    if not _lock.acquire(blocking=False):
        raise ScheduleBusyError(busy_message())
    try:
        _enter(activity)
        yield
    finally:
        _leave()
        _lock.release()


def busy_message() -> str:
    activity = _current_activity or "另一项排程"
    elapsed = int(monotonic() - _started_at) if _started_at else 0
    return f"{activity}正在进行中（已运行 {elapsed} 秒），请稍后重试"


def current_activity() -> str | None:
    return _current_activity


def _enter(activity: str) -> None:
    global _current_activity, _started_at, _depth
    if _depth == 0:
        _current_activity, _started_at = activity, monotonic()
    _depth += 1


def _leave() -> None:
    global _current_activity, _started_at, _depth
    _depth -= 1
    if _depth == 0:
        _current_activity, _started_at = None, 0.0

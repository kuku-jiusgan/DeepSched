from __future__ import annotations

from datetime import datetime, timedelta
from time import monotonic
import logging

from app.models import Project

_logger = logging.getLogger(__name__)

MAX_RECOMMENDATIONS = 20
SEARCH_TIME_LIMIT_SECONDS = 120

FEASIBLE = "feasible"          # 求解器找到了可行排程
INFEASIBLE = "infeasible"      # 求解器证明了排不下
UNDETERMINED = "undetermined"  # 求解超时，什么也没证明


def enumerate_verified_date_adjustments(
    db, scheduler, project_ids: list[int], original_deadlines: dict[int, datetime],
    horizon_end: datetime, generate_kwargs: dict, project_labels: dict[int, str] | None = None,
) -> list[dict]:
    """Enumerate minimal project-deadline adjustments verified by the solver."""
    project_ids = [project_id for project_id in project_ids if project_id in original_deadlines]
    project_ids = _projects_whose_deadline_can_bind(db, project_ids, generate_kwargs)
    priorities = _load_project_priorities(db, project_ids)
    candidates = _candidate_deadlines(db, project_ids, original_deadlines, horizon_end)
    search_deadline = monotonic() + SEARCH_TIME_LIMIT_SECONDS
    # 这里曾经先做一次"所有项目都放到求解视界还行不行"的全局预检，用来在完全
    # 没救时提前收工。现在每个项目自己会先试最远那天，同样能定论，预检就纯属
    # 多余了——而且它把所有结题日一起放宽，模型最松、求解器搜得最久，实测单这
    # 一次就要 18.2 秒，比它能省下的那几次快速否定贵得多。
    results: list[dict] = []
    # 每个项目单独出一套方案：只动它一个，其余项目原地不动，求它最短要延几天。
    # 方案之间互相独立，不存在"这 2 个项目需要一起调整"的组合方案——那种方案
    # 看不出每个项目为什么被牵进来，业务上也没法执行。扫到求解视界仍找不到可行
    # 日期的项目，直接不出方案。
    for project_id in _search_order(project_ids, original_deadlines):
        if monotonic() >= search_deadline:
            break
        adjustment = _first_verified_adjustment(
            db, scheduler, project_id, candidates, generate_kwargs, search_deadline,
        )
        if adjustment:
            results.append(_format_adjustment(
                adjustment, original_deadlines, project_labels or {}, priorities,
            ))
    return _sort_results(results)


def _projects_whose_deadline_can_bind(db, project_ids: list[int], generate_kwargs: dict) -> list[int]:
    """只保留结题日真的能影响这次求解的项目。

    结题日的作用是给**参与本次求解的任务**设完工上界。一个项目如果没有任务在
    求解集合里（它的活是固定时间槽，这次根本不会动），延它的结题日不会腾出任何
    资源，探测必然全部不可行——却要白白花掉整整一轮二分。测试2 就是这种情况：
    它的检测任务是固定槽，用户看到"延测试2 几天"的方案照做后毫无变化。
    """
    task_ids = generate_kwargs.get("task_ids") or []
    if not task_ids:
        return project_ids
    from app.models import Task

    rows = db.query(Task.project_id).filter(Task.id.in_(task_ids)).distinct().all()
    solvable = {project_id for (project_id,) in rows}
    current = generate_kwargs.get("current_project_id")
    if current is not None:
        solvable.add(current)
    kept = [project_id for project_id in project_ids if project_id in solvable]
    return kept or project_ids


def _candidate_deadlines(
    db, project_ids: list[int], original_deadlines: dict[int, datetime],
    horizon_end: datetime,
) -> dict[int, list[datetime]]:
    """候选结题日只取工作日。

    结题日是跟客户签的合同日期，落在周末或法定假日上没有意义；更实际的问题是它
    腾不出任何工时——排程只在工作时段里落任务，把结题日从周六挪到周日，可用工时
    一分钟都没多。线上就出过这种建议：原结题日 2026-09-12（周六），建议延到
    09-13（周日）。

    是否把周末算作工作时间由排程规则决定（include_weekends / include_holidays），
    所以这里按规则加日历判断，而不是简单地跳过周六周日。
    """
    from app.services.project_deadline_calendar_service import working_day_flags
    from app.services.scheduler_helpers import is_allowed_calendar_day, load_calendar_days

    starts = [original_deadlines[project_id] for project_id in project_ids]
    if not starts:
        return {}
    # 与本模块 _load_project_priorities 同一处理：搜索逻辑的单测传的是假的
    # db，拿不到日历就不过滤。生产路径一定是真实会话。
    if getattr(db, "query", None) is None:
        return _raw_candidates(project_ids, original_deadlines, horizon_end)
    include_weekends, include_holidays = working_day_flags(db)
    calendar_days = load_calendar_days(db, min(starts), horizon_end)

    result: dict[int, list[datetime]] = {}
    for project_id in project_ids:
        start = original_deadlines[project_id]
        span = (horizon_end.date() - start.date()).days
        days = _days_after(start, span, horizon_end)
        working = [
            day for day in days
            if is_allowed_calendar_day(
                day.date(), calendar_days, include_weekends, include_holidays,
            )
        ]
        # 视界内一个工作日都没有时不做过滤，宁可给出一个不理想的建议，也好过
        # 一声不吭地什么都不给。
        result[project_id] = working or days
    return result


def _raw_candidates(project_ids, original_deadlines, horizon_end) -> dict:
    return {
        project_id: _days_after(
            original_deadlines[project_id],
            (horizon_end.date() - original_deadlines[project_id].date()).days,
            horizon_end,
        )
        for project_id in project_ids
    }


def _days_after(start: datetime, span: int, horizon_end: datetime) -> list[datetime]:
    return [
        (start + timedelta(days=offset)).replace(
            hour=23, minute=59, second=0, microsecond=0,
        )
        for offset in range(1, span + 1)
        if (start + timedelta(days=offset)).date() <= horizon_end.date()
    ]


def _search_order(project_ids: list[int], original_deadlines: dict[int, datetime]) -> list[int]:
    """按结题日从早到晚试。

    卡住排程的通常是结题日最早的那个项目——它的任务被顶到期限之外，别的项目
    延多久都腾不出它需要的位置。此前按项目号顺序试，真正该延的项目排在后面时，
    要先在前面的项目上白试上百次。
    """
    return sorted(project_ids, key=lambda project_id: (original_deadlines[project_id], project_id))


def _first_verified_adjustment(
    db, scheduler, project_id: int, candidates, generate_kwargs, search_deadline,
):
    """找这个项目最短要延几天，找不到返回 None。

    延后结题日只放宽任务的完工上界，是单调放松：某个日期可行，则更晚的日期必然
    可行。于是候选序列上"前面全不可行、后面全可行"，可以二分。但每次试解都是
    一次完整排程（实测 4.3 秒，其中 CP-SAT 求解就占 2.7 秒），次数才是成本，
    所以在二分之外还做了两件事：

    先试**最远那天**。它不可行就意味着整段都不可行，一次就能断言"单独延这个
    项目没用"——否则要二分满 7 次才敢下同样的结论。一次搜索里通常多数项目都
    属于这种，此前光是证明它们无解就占掉大半时间。

    再从**近端倍增**试探（第 1、2、4、8… 天），命中后只在最后那一格里二分。
    实际答案几乎都很小（常见就是延 1 天），倍增两次就能收敛，而二分不论答案
    多小都要走满 log2(N) 次。

    5 秒没算出结论的（undetermined）当作"尚未证明可行"往右找。这样返回的日期
    仍然是求解器验证过可行的，只是真正的最小值那天恰好超时时会比它晚一点。
    """
    dates = candidates.get(project_id) or []
    if not dates or monotonic() >= search_deadline:
        return None

    def probe(index: int) -> str:
        return _probe_deadlines(db, scheduler, {project_id: dates[index]}, generate_kwargs)

    # 先试最近那天。实际答案几乎都很小（常见就是延 1 天），命中就直接拿到了
    # 真正的最小值，一次搞定。
    if probe(0) == FEASIBLE:
        return {project_id: dates[0]}

    last = len(dates) - 1
    if last < 1 or monotonic() >= search_deadline or probe(last) != FEASIBLE:
        return None

    # 倍增找到第一个可行的候选，同时把它左边那个不可行的记下来作为二分下界。
    low, high = 1, last
    step = 0
    while low + step < last and monotonic() < search_deadline:
        index = low + step
        if probe(index) == FEASIBLE:
            high = index
            break
        low = index + 1
        step = step * 2 + 1
    else:
        high = last

    while low < high and monotonic() < search_deadline:
        middle = (low + high) // 2
        if probe(middle) == FEASIBLE:
            high = middle
        else:
            low = middle + 1
    return {project_id: dates[high]} if low >= high else None


def _load_project_priorities(db, project_ids: list[int]) -> dict[int, int]:
    query = getattr(db, "query", None)
    if query is None:
        return {}
    return {
        project.id: int(project.priority or 999)
        for project in query(Project).filter(Project.id.in_(project_ids)).all()
    }


def _probe_deadlines(
    db, scheduler, deadlines: dict[int, datetime], generate_kwargs: dict,
) -> str:
    """在给定的一组结题日下，走**真实排程入口**试一次。

    判定排不排得下只能有一个口径。此前这里是拿排程失败当时抓下的 generate_kwargs
    直接重放一次求解（5 秒、只问可行性），而用户点下去真正走的是「保存并排程」
    入口——它在求解前还要删掉可移动任务的时间槽、重置任务状态，并且把「需要移动
    别的项目」当作独立结果。实测同一批候选里 12 个有 9 个判定不一致，而且全部是
    重放那条偏乐观：四套标着「求解器已验证」的方案只有一套真能排下去，用户按提示
    改完结题日，回来得到的还是同一句失败。

    代价是每次探测从约 0.46 秒涨到约 2.1 秒。宁可慢，也不能出现两套结论。

    必须真的改 Project.end_date 再跑：真实入口读的就是它，用覆盖参数验不出同一
    条路。全程在 savepoint 里，跑完回滚。
    """
    current_project_id = generate_kwargs.get("current_project_id")
    if current_project_id is None:
        return INFEASIBLE
    from app.services.project_plan_apply_service import apply_project_plan
    from app.services.schedule_deadline_recommendation_job_service import (
        suppress_recommendation_jobs,
    )

    savepoint = db.begin_nested()
    try:
        for project_id, deadline in deadlines.items():
            project = db.query(Project).filter(Project.id == project_id).first()
            if project is not None:
                project.end_date = deadline
        db.flush()
        # 真实入口失败时会顺手再排一个方案搜索作业。不挡住的话，搜索自己又触发
        # 一轮搜索，层层套下去——实测套了 170 秒。
        with suppress_recommendation_jobs():
            # preserve_existing=True 是入口自带的"试排"模式：内部不提交、自己用
            # savepoint 包住。不加这个的话它会真的提交，外层这个 savepoint 就
            # 拦不住了——探测会把改过的结题日和排程结果永久写进库。
            result = apply_project_plan(db, current_project_id, preserve_existing=True)
        return FEASIBLE if getattr(result, "status", "error") != "error" else INFEASIBLE
    except Exception:
        _logger.exception("候选结题日探测失败 deadlines=%s", sorted(deadlines))
        return INFEASIBLE
    finally:
        savepoint.rollback()


def _format_adjustment(
    adjustment: dict[int, datetime], originals: dict[int, datetime], labels: dict[int, str],
    priorities: dict[int, int] | None = None,
) -> dict:
    changes = [
        {
            "project_id": project_id,
            "project_label": labels.get(project_id, f"项目 {project_id}"),
            "original_deadline": originals[project_id].strftime("%Y-%m-%d"),
            "suggested_deadline": deadline.strftime("%Y-%m-%d"),
            "delay_days": (deadline.date() - originals[project_id].date()).days,
            "project_priority": (priorities or {}).get(project_id, 999),
        }
        for project_id, deadline in sorted(adjustment.items())
    ]
    return {
        "code": "",
        "kind": "verified_date_adjustment",
        "title": "、".join(f"{item['project_label']} 延期 {item['delay_days']} 天" for item in changes),
        "description": "；".join(
            f"{item['project_label']}结题日 {item['original_deadline']} → {item['suggested_deadline']}"
            for item in changes
        ),
        "projects": [item["project_id"] for item in changes],
        "changes": changes,
        "verified": True,
        "verification": "solver",
    }


def _sort_results(results: list[dict]) -> list[dict]:
    """按延期天数从小到大排。

    天数相同时，优先推低优先级的项目——同样延 2 天，动三级项目比动一级项目
    代价小。最后按项目号兜底，保证同样的输入永远给出同样的顺序。
    """
    return sorted(results, key=lambda item: (
        sum(change["delay_days"] for change in item["changes"]),
        -max(change["project_priority"] for change in item["changes"]),
        tuple(item["projects"]),
    ))[:MAX_RECOMMENDATIONS]

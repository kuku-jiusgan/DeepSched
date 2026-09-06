import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.errors import DomainConflictError
from app.models import Instrument, Project, Task, TimeSlot, User
from app.services.project_deadline_calendar_service import working_day_flags
from app.services.scheduler_helpers import is_allowed_calendar_day, load_calendar_days
from app.services.task_execution_service import start_task_execution
from app.services.task_pause_service import pause_and_switch_task
from app.services.task_pause_switch_diagnosis_service import (
    diagnose_pause_switch_failure,
)


def _next_working_day() -> datetime:
    """明天起的第一个工作日（零点）。

    用例里的时间槽必须落在工作日的 8:30-20:00 之内。写死日期会随着时间推移变成
    过去，排程不会往回排；直接用"明天"则在周五、周六运行时落到休息日。
    """
    day = (datetime.now() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


class PauseSwitchFailureDiagnosisTest(unittest.TestCase):
    """暂停并切换失败时，报错必须说清是哪个项目要延期几天。

    这条路以前复用项目计划排程的失败诊断：文案按"当前项目 + 仪器工时缺口"的
    启发式生成，调整方案由后台作业拿「保存并开始排程」入口去验证——验的不是这次
    切换。现在改成把这次切换原样再交给求解器跑一遍、只临时放开各项目结题日期，
    排出来做到哪一天就是要延到哪一天。
    """

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        self.operator = User(username="tech", display_name="张三", role="技术员")
        self.instrument = Instrument(code="LCMS-01", name="液质联用仪")
        self.project_a = Project(code="XM2026001", name="项目A")
        self.project_b = Project(code="XM2026002", name="项目B")
        self.db.add_all([self.operator, self.instrument, self.project_a, self.project_b])
        self.db.flush()
        now = _next_working_day().replace(hour=10)
        self.source_task = Task(
            project_id=self.project_a.id, name="方法开发A", task_type="FFKF_001",
            requires_instrument=True, assignee_id=self.operator.id, status="scheduled",
        )
        self.target_task = Task(
            project_id=self.project_b.id, name="方法开发B", task_type="FFKF_001",
            requires_instrument=True, assignee_id=self.operator.id, status="scheduled",
        )
        self.db.add_all([self.source_task, self.target_task])
        self.db.flush()
        self.source_slot = TimeSlot(
            task_id=self.source_task.id, instrument_id=self.instrument.id,
            plan_start=now - timedelta(hours=1), plan_end=now + timedelta(hours=2),
            status="scheduled", tier="confirmed",
        )
        self.target_slot = TimeSlot(
            task_id=self.target_task.id, instrument_id=self.instrument.id,
            plan_start=now + timedelta(hours=2), plan_end=now + timedelta(hours=5),
            status="scheduled", tier="confirmed",
        )
        self.db.add_all([self.source_slot, self.target_slot])
        self.db.commit()
        start_task_execution(self.db, self.source_slot.id, self.operator.id)
        self.db.commit()
        # 源任务所在项目的结题日期近在眼前：切换让出仪器之后，剩下的工时无论
        # 怎么排都会越过它。
        self.project_a.end_date = datetime.now() + timedelta(hours=1)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _switch(self) -> DomainConflictError:
        with self.assertRaises(DomainConflictError) as raised:
            pause_and_switch_task(
                self.db, self.source_slot.id, "切换任务", self.operator, self.target_slot.id,
            )
        return raised.exception

    def test_failure_names_the_project_and_the_days_to_extend(self):
        failure = self._switch().detail["pause_switch_failure"]

        self.assertEqual("project_deadline_overrun", failure["kind"])
        self.assertEqual(1, len(failure["overruns"]))
        overrun = failure["overruns"][0]
        self.assertEqual(self.project_a.id, overrun["project_id"])
        self.assertEqual("XM2026001 · 项目A", overrun["project_label"])
        self.assertEqual("方法开发A", overrun["blocking_task_name"])
        self.assertEqual("张三", overrun["blocking_task_assignee"])
        self.assertGreaterEqual(overrun["delay_days"], 1)

    def test_reason_and_plan_use_the_same_number_of_days(self):
        """原因说延期几天，方案就是把结题日期延这几天，两个数必须是同一个。"""
        overrun = self._switch().detail["pause_switch_failure"]["overruns"][0]

        deadline = datetime.strptime(overrun["deadline"], "%Y-%m-%d").date()
        suggested = datetime.strptime(overrun["suggested_deadline"], "%Y-%m-%d").date()
        self.assertEqual(overrun["delay_days"], (suggested - deadline).days)

    def test_suggested_deadline_falls_on_a_working_day(self):
        """结题日期落在周末或法定假日上腾不出任何工时，等于没给建议。"""
        overrun = self._switch().detail["pause_switch_failure"]["overruns"][0]

        suggested = datetime.strptime(overrun["suggested_deadline"], "%Y-%m-%d")
        include_weekends, include_holidays = working_day_flags(self.db)
        calendar_days = load_calendar_days(self.db, suggested, suggested)
        self.assertTrue(is_allowed_calendar_day(
            suggested.date(), calendar_days, include_weekends, include_holidays,
        ))

    def test_diagnosis_leaves_the_schedule_untouched(self):
        """诊断要压上切换锚点、作废时间槽、再跑一遍排程，跑完必须一个字节都不落库。"""
        before = self._snapshot()

        diagnose_pause_switch_failure(
            self.db, self.source_slot, self.target_slot, datetime.now(),
        )

        self.db.expire_all()
        self.assertEqual(before, self._snapshot())

    def _snapshot(self) -> dict:
        slots = {
            slot.id: (
                slot.plan_start, slot.plan_end, slot.actual_end,
                slot.status, slot.lifecycle_status,
            )
            for slot in self.db.query(TimeSlot).all()
        }
        tasks = {task.id: task.status for task in self.db.query(Task).all()}
        return {"slots": slots, "tasks": tasks}

    def test_only_a_proved_infeasible_becomes_a_constraint_conflict(self):
        """只有求解器证明了 INFEASIBLE，才敢说"改日期解决不了"。

        放开结题日期会抽掉一大批上界，搜索空间随之变大，求解器很容易在时限内
        证不出结论而返回 UNKNOWN。把 UNKNOWN 当成"排不下"，给出的就是一句与
        事实相反的结论，让人白改一遍日期。
        """
        cases = {"INFEASIBLE": "scheduling_conflict", "UNKNOWN": "undetermined"}
        for solver_status, expected in cases.items():
            with self.subTest(solver_status=solver_status):
                # 上一轮失败的切换在会话里留了一堆未提交的改动，不清掉的话下一轮
                # 一开始就不是 setUp 里的那个世界了。
                self.db.rollback()
                with patch(
                    "app.services.task_pause_solver_service.replan_resource_closure",
                    return_value={
                        "status": "error",
                        "message": "未找到可行排程",
                        "solver_status": solver_status,
                    },
                ):
                    failure = self._switch().detail["pause_switch_failure"]

                self.assertEqual(expected, failure["kind"])
                self.assertEqual("未找到可行排程", failure["solver_message"])

    def test_no_project_plan_recommendation_job_is_queued(self):
        """后台方案作业拿「保存并开始排程」入口去验证，验的不是这次切换。"""
        with patch(
            "app.services.scheduler_failure_response.create_deadline_recommendation_job",
        ) as job:
            self._switch()

        job.assert_not_called()


if __name__ == "__main__":
    unittest.main()

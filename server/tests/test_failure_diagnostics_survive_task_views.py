"""求解失败时，诊断必须仍然给出准确的根因。

求解主链路的任务已经换成值对象，但诊断这条路还需要实体：它要从任务反向拿项目的
全量叶子任务、顺着父链上溯、读时间槽——这些在值对象上都没有展开。

危险之处在于**它不会报错**。值对象上 `project.tasks` 是空元组，诊断会静默退化成
"只看这一个任务"。实测同一个排不下的场景：缺口从 74 小时变成 0，根因从「计划内
仪器工时不足」变成笼统的「受排程约束限制」——诊断照常返回，内容却是错的。

所以 build_failure_response 会按 id 把任务换回实体。这条测试钉住那个结果。
"""

import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot
from app.services.scheduler import SchedulerService

NOW = datetime(2026, 9, 7, 9, 0, 0)


class FailureDiagnosticsSurviveTaskViewsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()
        instruments = []
        for code in ("INST-A", "INST-B"):
            instrument = Instrument(
                code=code, name=code, availability_status="available", status="idle",
                effective_work_start="08:30", effective_work_end="20:00",
            )
            self.db.add(instrument)
            instruments.append(instrument)
        self.db.flush()
        ids = [item.id for item in instruments]
        # 每个任务单独都排得下，串起来才装不下——只有求解器能发现，因此会走到
        # 深度诊断。若在建变量阶段就因单任务窗口不足返回，这条路根本到不了。
        # 别的项目先占掉一段仪器时间。诊断要读 slot.task.project.code 之类，
        # 没有固定槽的话那条路根本走不到——第一版这条测试就是这样漏掉的：任务换回
        # 了实体、时间槽没换，直到语料里加进占位槽才暴露出 AttributeError。
        other = Project(code="DIAG-OCCUPY", name="占位项目", priority=5,
                        start_date=NOW, end_date=NOW + timedelta(days=30))
        self.db.add(other)
        self.db.flush()
        occupied = Task(project_id=other.id, name="占位任务", task_type="test",
                        status="scheduled", est_duration_hours=6,
                        requires_instrument=True, requires_human=False,
                        instrument_ids=[ids[0]])
        self.db.add(occupied)
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=occupied.id, schedule_run_id="seed", instrument_id=ids[0],
            plan_start=NOW + timedelta(days=1), plan_end=NOW + timedelta(days=1, hours=6),
            tier="confirmed", status="scheduled", lifecycle_status="active",
        ))
        self.db.flush()
        project = Project(code="DIAG-1", name="诊断项目", priority=1,
                          start_date=NOW, end_date=NOW + timedelta(days=4))
        self.db.add(project)
        self.db.flush()
        tasks = []
        for name in ("前序任务", "后续任务"):
            task = Task(project_id=project.id, name=name, task_type="test",
                        status="pending", est_duration_hours=30,
                        requires_instrument=True, requires_human=False,
                        instrument_ids=ids)
            self.db.add(task)
            self.db.flush()
            tasks.append(task)
        self.db.add(TaskDependency(task_id=tasks[1].id, predecessor_id=tasks[0].id))
        self.project_id = project.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_the_diagnosis_names_the_real_bottleneck(self):
        result = SchedulerService(self.db)._generate(
            now=NOW, current_project_id=self.project_id,
            commit=False, emit_advance_notifications=False,
        )

        self.assertEqual("error", result["status"])
        failure = result.get("schedule_failure")
        self.assertIsNotNone(failure, "失败响应里应当带诊断")
        self.assertEqual(
            "instrument_capacity", failure.get("kind"),
            "诊断退化成了笼统结论——任务大概率没被换回实体：%s" % failure.get("summary"),
        )
        # 两个任务各 30 小时，同属一个占用组，所需工时必须是 60 而不是 30。
        # （instruments 那一栏是按每台候选仪器各计一次的聚合，不适合做这个判断。）
        required = max(item["required_hours"] for item in failure["groups"])
        self.assertEqual(
            60.0, required,
            "所需工时只算进了一个任务，说明诊断看到的任务集合不完整",
        )
        self.assertGreater(max(item["deficit_hours"] for item in failure["instruments"]), 0)
        # 占用明细要认得出占位项目——这一条只有在时间槽也换回实体时才成立。
        occupancy = json.dumps(failure.get("occupancy"), ensure_ascii=False)
        self.assertIn("DIAG-OCCUPY", occupancy, "占用明细没认出占位项目")


if __name__ == "__main__":
    unittest.main()

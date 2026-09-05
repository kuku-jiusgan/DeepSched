"""建模阶段不许查数据库。

求解器本该是纯函数：世界进去，计划出来。当前的架构缺陷是它通过数据库跟应用层
通信——先删时间槽、改任务状态、flush，再回头查库才知道哪些资源被占。于是"一个
假设"的最小单元变成了一个事务而不是一个值，探测要包 savepoint、要防止真提交、
要全局互斥，并行探测还会撞行锁。

把求解输入收拢成 PlanningProblem 是在掰回这一点，而这条测试是它的护栏：一旦有人
在建模过程中间加一次查询，这里立刻失败。靠人工审查守不住——全仓 37 个 ORM 关系
全是默认延迟加载，一次属性访问就可能悄悄发一条 SQL。

阶段边界取"CpModel 对象创建"到"求解日志开始写"：前者之前是装载，后者之后是观测
与求解，都允许碰库。中间这一段是纯建模。
"""

import unittest
from datetime import datetime, timedelta

from ortools.sat.python import cp_model
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency
from app.services.scheduler import SchedulerService


class _StopBeforeSolving(Exception):
    pass


class ModelBuildingTouchesNoDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        start = datetime.now().replace(hour=8, minute=30, second=0, microsecond=0)

        for index in range(2):
            self.db.add(Instrument(
                code="PURE-%d" % index, name="纯建模仪器%d" % index,
                availability_status="available", status="idle",
            ))
        project = Project(
            code="PURE-1", name="纯建模项目", priority=1,
            start_date=start, end_date=start + timedelta(days=30),
        )
        self.db.add(project)
        self.db.flush()
        tasks = []
        for index in range(3):
            task = Task(
                project_id=project.id, name="任务%d" % index, task_type="test",
                status="pending", est_duration_hours=2,
                requires_instrument=False, requires_human=False,
            )
            self.db.add(task)
            self.db.flush()
            tasks.append(task)
        # 带上依赖，才会走到前置任务完工时间那条路径。
        self.db.add(TaskDependency(task_id=tasks[1].id, predecessor_id=tasks[0].id))
        self.db.add(TaskDependency(task_id=tasks[2].id, predecessor_id=tasks[1].id))
        self.project_id = project.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_no_sql_between_creating_the_model_and_solving_it(self):
        statements: list[str] = []
        building = {"active": False}

        @event.listens_for(self.engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            if building["active"]:
                statements.append(statement)

        real_init = cp_model.CpModel.__init__
        real_solve = cp_model.CpSolver.Solve

        def _init(model, *args, **kwargs):
            real_init(model, *args, **kwargs)
            building["active"] = True

        def _solve(solver, model, *args, **kwargs):
            building["active"] = False
            raise _StopBeforeSolving()

        cp_model.CpModel.__init__ = _init
        cp_model.CpSolver.Solve = _solve
        try:
            SchedulerService(self.db)._generate(
                current_project_id=self.project_id,
                commit=False,
                emit_advance_notifications=False,
                include_failure_diagnostics=False,
            )
        except _StopBeforeSolving:
            pass
        finally:
            cp_model.CpModel.__init__ = real_init
            cp_model.CpSolver.Solve = real_solve
            building["active"] = False

        self.assertEqual(
            [], statements,
            "建模阶段发生了 %d 条数据库查询：\n%s" % (
                len(statements), "\n".join(statements[:5]),
            ),
        )


if __name__ == "__main__":
    unittest.main()

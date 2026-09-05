"""建模阶段不许查数据库。

求解器本该是纯函数：世界进去，计划出来。当前的架构缺陷是它通过数据库跟应用层
通信——先删时间槽、改任务状态、flush，再回头查库才知道哪些资源被占。于是"一个
假设"的最小单元变成了一个事务而不是一个值，探测要包 savepoint、要防止真提交、
要全局互斥，并行探测还会撞行锁。

把求解输入收拢成 PlanningProblem 是在掰回这一点，而这条测试是它的护栏：一旦有人
在建模过程中间加一次查询，这里立刻失败。靠人工审查守不住——全仓 37 个 ORM 关系
全是默认延迟加载，一次属性访问就可能悄悄发一条 SQL。

阶段边界取"CpModel 对象创建"到"求解日志开始写"：前者之前是装载，后者之后是观测
与求解，都允许碰库（求解日志要按 id 查出任务名写进日志文件，那是观测，不是建模）。
中间这一段是纯建模。

边界必须钉在日志开始写的那一刻，不能图省事钉到 Solve：那样日志自己发的查询会落在
统计区间内，护栏就得容忍非零条数，也就再也分不清"多出来的查询"是日志的还是有人
在建模里新加的。
"""

import unittest
from datetime import datetime, timedelta

from ortools.sat.python import cp_model
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Instrument, Project, Task, TaskDependency, TimeSlot
from app.services.scheduler import SchedulerService
from app.services.scheduler_solver_trace_service import SolverTrace


class _StopBeforeSolving(Exception):
    pass


class ModelBuildingTouchesNoDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        start = datetime.now().replace(hour=8, minute=30, second=0, microsecond=0)

        instruments = []
        for index in range(2):
            instrument = Instrument(
                code="PURE-%d" % index, name="纯建模仪器%d" % index,
                availability_status="available", status="idle",
            )
            self.db.add(instrument)
            instruments.append(instrument)
        self.db.flush()
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
                requires_instrument=True, requires_human=False,
                instrument_ids=[instruments[0].id],
            )
            self.db.add(task)
            self.db.flush()
            tasks.append(task)
        # 带上依赖，才会走到前置任务完工时间那条路径。
        self.db.add(TaskDependency(task_id=tasks[1].id, predecessor_id=tasks[0].id))
        self.db.add(TaskDependency(task_id=tasks[2].id, predecessor_id=tasks[1].id))
        # 必须有既有时间槽：没有的话求解日志的固定槽登记表是空的，它那几条查询
        # 压根不会发生，边界定在哪都"通过"，这条测试就成了空转。
        other = Task(
            project_id=project.id, name="占位任务", task_type="test",
            status="scheduled", est_duration_hours=2,
            requires_instrument=True, requires_human=False,
            instrument_ids=[instruments[0].id],
        )
        self.db.add(other)
        self.db.flush()
        self.db.add(TimeSlot(
            task_id=other.id, schedule_run_id="run-0", instrument_id=instruments[0].id,
            plan_start=start + timedelta(days=1),
            plan_end=start + timedelta(days=1, hours=2),
            tier="confirmed", status="scheduled", lifecycle_status="active",
        ))
        self.project_id = project.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_no_sql_between_creating_the_model_and_solving_it(self):
        statements: list[str] = []
        building = {"active": False, "entered": False, "traced": False}

        @event.listens_for(self.engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            if building["active"]:
                statements.append(statement)

        real_init = cp_model.CpModel.__init__
        real_solve = cp_model.CpSolver.Solve

        def _init(model, *args, **kwargs):
            real_init(model, *args, **kwargs)
            building["active"] = True
            building["entered"] = True

        def _solve(solver, model, *args, **kwargs):
            building["active"] = False
            raise _StopBeforeSolving()

        real_trace_init = SolverTrace.__init__

        def _trace_init(trace, *args, **kwargs):
            # 求解日志一开始写，建模阶段就结束了。日志本身要按 id 查任务名写进
            # 文件，那是观测，不该算进"建模阶段查库"。
            building["active"] = False
            building["traced"] = True
            real_trace_init(trace, *args, **kwargs)

        cp_model.CpModel.__init__ = _init
        cp_model.CpSolver.Solve = _solve
        SolverTrace.__init__ = _trace_init
        try:
            self.result = SchedulerService(self.db)._generate(
                current_project_id=self.project_id,
                commit=False,
                emit_advance_notifications=False,
            )
        except _StopBeforeSolving:
            pass
        finally:
            cp_model.CpModel.__init__ = real_init
            cp_model.CpSolver.Solve = real_solve
            SolverTrace.__init__ = real_trace_init
            building["active"] = False

        # 先确认这条测试真的走到了建模：_generate 有十几处提前返回，任何一处都会让
        # 上面的统计恒为空，这条护栏就成了永远通过的摆设。
        self.assertTrue(building["entered"], "没有走到建模阶段，这条测试是空转：%s" % (
            getattr(self, "result", None),))
        self.assertTrue(building["traced"], "没有走到求解日志，阶段边界没被验到：%s" % (
            getattr(self, "result", None),))
        self.assertEqual(
            [], statements,
            "建模阶段发生了 %d 条数据库查询：\n%s" % (
                len(statements), "\n".join(statements[:5]),
            ),
        )


    def test_the_solver_log_may_query_without_failing_the_guard(self):
        """边界本身也要验：求解日志之后的查询不算违规。

        上一条测试证明的是"建模阶段没有查询"，但它无法区分"确实没有"和"边界画得
        太宽以致于压根没检查到"。这里直接往求解日志里塞一次查询——它必须被放行，
        否则说明边界仍然钉在 Solve 上，日志自己发的 SQL 会污染统计。
        """
        from app.models import Task as _Task

        statements: list[str] = []
        building = {"active": False}

        @event.listens_for(self.engine, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            if building["active"]:
                statements.append(statement)

        real_init = cp_model.CpModel.__init__
        real_solve = cp_model.CpSolver.Solve
        real_trace_init = SolverTrace.__init__
        db = self.db

        def _init(model, *args, **kwargs):
            real_init(model, *args, **kwargs)
            building["active"] = True

        def _trace_init(trace, *args, **kwargs):
            building["active"] = False
            real_trace_init(trace, *args, **kwargs)
            db.query(_Task).first()          # 日志按 id 查任务名，就是这种查询

        def _solve(solver, model, *args, **kwargs):
            building["active"] = False
            raise _StopBeforeSolving()

        cp_model.CpModel.__init__ = _init
        cp_model.CpSolver.Solve = _solve
        SolverTrace.__init__ = _trace_init
        try:
            SchedulerService(self.db)._generate(
                current_project_id=self.project_id,
                commit=False,
                emit_advance_notifications=False,
            )
        except _StopBeforeSolving:
            pass
        finally:
            cp_model.CpModel.__init__ = real_init
            cp_model.CpSolver.Solve = real_solve
            SolverTrace.__init__ = real_trace_init
            building["active"] = False

        self.assertEqual([], statements, "日志阶段的查询被误算进了建模阶段")


if __name__ == "__main__":
    unittest.main()

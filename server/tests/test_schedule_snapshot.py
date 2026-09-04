import pickle
import threading
import unittest
from datetime import datetime

from app.services.schedule_snapshot import (
    ProjectSnapshot,
    ScheduleSnapshot,
    TaskSnapshot,
)


class ScheduleSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = ScheduleSnapshot(
            projects={1: ProjectSnapshot(1, datetime(2026, 9, 30), 1)},
            tasks={1: TaskSnapshot(1, 1, "pending", 2.0, None, (3,), ())},
            instruments={}, time_slots=(), maintenance_windows=(), bridge_reservations=(), dependencies=(), calendar_days=(), rule_params={}, rule_enabled={},
            captured_at=datetime(2026, 9, 1),
        )

    def test_fingerprint_is_stable(self):
        self.assertEqual(self.snapshot.fingerprint(), self.snapshot.fingerprint())

    def test_deadline_override_does_not_mutate_snapshot(self):
        override = datetime(2026, 10, 15)
        deadlines = self.snapshot.with_deadline_overrides({1: override})
        self.assertEqual(override, deadlines[1])
        self.assertEqual(datetime(2026, 9, 30), self.snapshot.projects[1].end_date)

    def test_unknown_deadline_project_fails_fast(self):
        with self.assertRaises(ValueError):
            self.snapshot.with_deadline_overrides({99: datetime(2026, 10, 1)})

    def test_simulation_context_isolated_and_picklable(self):
        from app.services.schedule_snapshot import SimulationContext

        context = SimulationContext(self.snapshot, {})
        candidate = context.fork({1: datetime(2026, 10, 15)})
        self.assertEqual({}, context.deadline_overrides)
        self.assertEqual(datetime(2026, 10, 15), candidate.deadline_overrides[1])
        restored = pickle.loads(pickle.dumps(candidate))
        self.assertEqual(candidate, restored)

    def test_simulation_context_requires_non_persistent_mode(self):
        from app.services.scheduler import SchedulerService
        from app.services.schedule_snapshot import SimulationContext

        result = SchedulerService(object())._generate(
            current_project_id=1,
            simulation_context=SimulationContext(self.snapshot, {}),
            commit=True,
            feasibility_only=True,
        )
        self.assertEqual("error", result["status"])


if __name__ == "__main__":
    unittest.main()


class SimulationWriteIsolationTest(unittest.TestCase):
    """模拟求解期间数据库写入次数必须为 0。

    方案搜索一次要试上百个候选结题日。它以前靠"改 Project.end_date、求解、回滚"
    来试，既污染事务又和真实排程抢锁；改成结题日覆盖之后这条边界必须有测试钉住，
    否则哪天有人在求解路径上加一句写库，回归时没人会发现。
    """

    WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "DROP", "CREATE")

    def _count_writes(self, engine, action):
        from sqlalchemy import event

        statements = []

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            head = statement.lstrip().split(" ", 1)[0].upper()
            if head in self.WRITE_PREFIXES:
                statements.append(statement.strip().split("\n")[0][:120])

        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            action()
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)
        return statements

    def test_deadline_probe_writes_nothing(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.core.database import Base
        from app.models import Project, Task
        from app.services.schedule_snapshot import SimulationContext, capture_schedule_snapshot
        from app.services.scheduler import SchedulerService
        from app.services.scheduler_deadline_recommendation import _probe_deadlines

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            project = Project(code="SIM-1", name="模拟项目", priority=3,
                              end_date=datetime(2026, 9, 30, 23, 59, 59))
            db.add(project)
            db.flush()
            task = Task(project_id=project.id, name="方法开发", task_type="test",
                        status="pending", est_duration_hours=2, requires_instrument=False,
                        requires_human=False)
            db.add(task)
            db.commit()

            snapshot = capture_schedule_snapshot(db, {project.id}, {task.id})
            context = SimulationContext(snapshot, {})
            scheduler = SchedulerService(db)
            verdicts = []
            writes = self._count_writes(engine, lambda: verdicts.append(_probe_deadlines(
                db, scheduler,
                {project.id: datetime(2026, 10, 5, 23, 59, 59)},
                {"current_project_id": project.id, "task_ids": [task.id]},
                context,
            )))

            # 先确认这次探测真的跑起来了，否则"零写入"可能只是因为它早早报错返回。
            self.assertIn(verdicts[0], {"feasible", "infeasible", "undetermined"})
            self.assertEqual([], writes, f"模拟求解写了库：{writes}")
            db.refresh(project)
            self.assertEqual(datetime(2026, 9, 30, 23, 59, 59), project.end_date)
        finally:
            db.close()


class SimulationLockIsolationTest(unittest.TestCase):
    """模拟求解不持全局排程锁。

    模拟只读、不落库，与真实排程没有互斥关系。以前它照样走 schedule_run_lock，
    于是方案搜索的几百次候选只能一个一个排队，还会把真实排程挡在后面——而这把
    锁是非阻塞获取的，真实排程撞上就直接收到"正在计算中"。
    """

    def test_simulation_runs_while_the_lock_is_held(self):
        from app.services.schedule_run_lock_service import SCHEDULE_RUN, schedule_run_lock
        from app.services.scheduler import SchedulerService
        from app.services.schedule_snapshot import SimulationContext

        snapshot = ScheduleSnapshot(
            projects={1: ProjectSnapshot(1, datetime(2026, 9, 30), 1)},
            tasks={}, instruments={}, time_slots=(), maintenance_windows=(),
            bridge_reservations=(), dependencies=(), calendar_days=(),
            rule_params={}, rule_enabled={}, captured_at=datetime(2026, 9, 1),
        )
        calls = []

        class Recording(SchedulerService):
            def __init__(self):
                pass

            def _generate(self, *args, **kwargs):
                calls.append(kwargs.get("simulation_context"))
                return {"status": "ok"}

        holder = threading.Thread(target=_hold_lock_briefly, args=(schedule_run_lock, SCHEDULE_RUN))
        holder.start()
        _LOCK_HELD.wait(2)
        try:
            result = Recording().generate(
                current_project_id=1,
                simulation_context=SimulationContext(snapshot, {}),
            )
        finally:
            _RELEASE_LOCK.set()
            holder.join(2)

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, len(calls))


_LOCK_HELD = threading.Event()
_RELEASE_LOCK = threading.Event()


def _hold_lock_briefly(schedule_run_lock, activity):
    with schedule_run_lock(activity):
        _LOCK_HELD.set()
        _RELEASE_LOCK.wait(5)

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, TaskDependency, TaskTypeConfig, User
from app.services.project_plan_template_service import (
    ProjectPlanTemplateInvalidError,
    ProjectPlanTemplatePermissionError,
    import_standard_plan,
)


class ProjectPlanTemplateServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.manager = User(id=1, username="manager", display_name="项目负责人", role="分析员")
        self.other_user = User(id=2, username="other", display_name="其他人员", role="分析员")
        self.project = Project(
            id=1,
            name="标准项目",
            code="TPL-001",
            estimated_hours=123.45,
            manager_id=1,
        )
        self.db.add_all([self.manager, self.other_user, self.project])
        for index, code in enumerate(["FFKF_001", "QCFA_001", "FFYZ_001", "ZXBG_001"]):
            self.db.add(TaskTypeConfig(
                name=code,
                code=code,
                resource_type="both",
                is_active=True,
                sort_order=index,
            ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_import_creates_standard_chain_with_exact_project_hours(self):
        result = import_standard_plan(self.db, self.project.id, self.manager)

        tasks = self.db.query(Task).filter(Task.project_id == self.project.id).all()
        by_name = {task.name: task for task in tasks}
        dependencies = {
            (dependency.predecessor_id, dependency.task_id)
            for dependency in self.db.query(TaskDependency).all()
        }
        work_tasks = [task for task in tasks if not task.is_external_gate]

        self.assertEqual(4, len(tasks))
        group = by_name["标准计划1"]
        self.assertEqual("group", group.task_type)
        self.assertTrue(all(task.parent_id == group.id for task in tasks if task.id != group.id))
        self.assertAlmostEqual(92.59, sum(task.est_duration_hours or 0 for task in work_tasks))
        self.assertEqual(
            ["方法开发", "方案撰写", "方案签批"],
            [task.name for task in result.tasks],
        )
        self.assertEqual([70.0, 5.0], [
            task.percentage for task in result.tasks if not task.is_approval_restriction
        ])
        self.assertIsNone(by_name["方案签批"].est_duration_hours)
        self.assertEqual(self.project.manager_id, by_name["方案签批"].assignee_id)
        self.assertNotIn("方法验证", by_name)
        self.assertNotIn("报告撰写", by_name)
        self.assertIn((by_name["方法开发"].id, by_name["方案撰写"].id), dependencies)
        self.assertIn((by_name["方案撰写"].id, by_name["方案签批"].id), dependencies)
        self.assertEqual(
            ["方法开发", "方案撰写", "方案签批"],
            [task.name for task in sorted(
                (task for task in tasks if task.parent_id == group.id),
                key=lambda task: task.plan_order,
            )],
        )

    def test_existing_plan_can_append_another_template_group(self):
        import_standard_plan(self.db, self.project.id, self.manager)
        import_standard_plan(self.db, self.project.id, self.manager)

        groups = self.db.query(Task).filter(Task.task_type == "group").order_by(Task.id).all()
        self.assertEqual(["标准计划1", "标准计划2"], [task.name for task in groups])
        for group in groups:
            self.assertEqual(3, self.db.query(Task).filter(Task.parent_id == group.id).count())

    def test_non_manager_cannot_import_template(self):
        with self.assertRaises(ProjectPlanTemplatePermissionError):
            import_standard_plan(self.db, self.project.id, self.other_user)

    def test_project_requires_estimated_hours(self):
        self.project.estimated_hours = None
        self.db.commit()

        with self.assertRaises(ProjectPlanTemplateInvalidError):
            import_standard_plan(self.db, self.project.id, self.manager)


if __name__ == "__main__":
    unittest.main()

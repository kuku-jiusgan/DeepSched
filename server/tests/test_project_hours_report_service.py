import unittest
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Task, User
from app.services.project_hours_report_service import build_project_hours_report, export_project_hours_report


class ProjectHoursReportServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(id=1, username="manager", display_name="项目负责人", role="分析所所长")
        self.project = Project(id=1, code="P-001", name="项目一", manager_id=1)
        parent = Task(
            id=1, project_id=1, name="LCMS方法开发", task_type="manual",
            est_duration_hours=12, status="running", plan_order=1,
        )
        child_a = Task(
            id=2, project_id=1, parent_id=1, name="方案撰写", task_type="manual",
            est_duration_hours=4, status="completed", plan_order=1,
        )
        child_b = Task(
            id=3, project_id=1, parent_id=1, name="方法验证", task_type="instrument",
            est_duration_hours=8, status="running", plan_order=2,
        )
        self.db.add_all([self.user, self.project, parent, child_a, child_b])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @patch("app.services.project_hours_report_service.task_actual_hours_map")
    def test_rolls_up_project_and_parent_actual_hours(self, actual_hours_map):
        actual_hours_map.return_value = {2: 3.5, 3: 6.0}

        report = build_project_hours_report(self.db, self.user)

        self.assertEqual(1, report.project_count)
        self.assertEqual(12, report.planned_hours)
        self.assertEqual(9.5, report.actual_hours)
        self.assertEqual(9.5, report.items[0].tasks[0].actual_hours)
        self.assertEqual(["LCMS方法开发", "方案撰写", "方法验证"], [task.task_name for task in report.items[0].tasks])

    @patch("app.services.project_hours_report_service.task_actual_hours_map", return_value={2: 3.5, 3: 6.0})
    def test_exports_summary_and_task_detail_sheets(self, _actual_hours_map):
        report = build_project_hours_report(self.db, self.user)

        workbook = load_workbook(export_project_hours_report(report))

        self.assertEqual(["项目汇总", "任务明细"], workbook.sheetnames)
        self.assertEqual("P-001", workbook["项目汇总"]["A2"].value)
        self.assertEqual("LCMS方法开发", workbook["任务明细"]["C2"].value)

    @patch("app.services.project_hours_report_service.task_actual_hours_map", return_value={2: 3.5, 3: 6.0})
    def test_filters_projects_by_keyword(self, _actual_hours_map):
        self.assertEqual(1, build_project_hours_report(self.db, self.user, keyword="P-001").project_count)
        self.assertEqual(1, build_project_hours_report(self.db, self.user, keyword="项目负责人").project_count)
        self.assertEqual(0, build_project_hours_report(self.db, self.user, keyword="不存在").project_count)


if __name__ == "__main__":
    unittest.main()

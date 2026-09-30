import unittest
from datetime import datetime

from openpyxl import load_workbook

from app.services.audit_log_export_service import export_audit_logs
from app.services.audit_log_presentation_service import present_audit_record


class AuditLogExportServiceTest(unittest.TestCase):
    def test_exports_readable_audit_log_workbook(self):
        output = export_audit_logs([{
            "created_at": datetime(2026, 8, 17, 9, 30, 5),
            "user_name": "system",
            "action": "task_paused",
            "target_type": "task",
            "target_id": 214,
            "detail": {"reason": "等待样品", "target_display": "V9062检测"},
            "category_label": "任务管理",
            "action_label": "暂停任务",
            "target_display": "V9062检测",
            "result": "success",
            "summary": "暂停任务【V9062检测】：等待样品",
            "changes": [],
            "business_detail": {"reason": "等待样品"},
        }])

        sheet = load_workbook(output)["操作日志"]

        self.assertEqual(
            ["时间", "操作人", "分类", "操作", "对象", "结果", "摘要", "变更详情"],
            [cell.value for cell in sheet[1]],
        )
        self.assertEqual("2026-08-17 09:30:05", sheet["A2"].value)
        self.assertEqual("系统自动任务", sheet["B2"].value)
        self.assertEqual("任务管理", sheet["C2"].value)
        self.assertEqual("暂停任务", sheet["D2"].value)
        self.assertEqual("V9062检测", sheet["E2"].value)
        self.assertIn("等待样品", sheet["G2"].value)

    def test_exports_presented_details_without_english_structure_keys(self):
        record = present_audit_record({
            "created_at": datetime(2026, 9, 10, 16, 33, 54),
            "user_name": "admin", "action": "project_updated",
            "target_type": "project", "target_id": 262,
            "detail": {
                "target_display": "XM2026262 · 氯雷他定口服溶液方法开发",
                "changes": [{"field": "end_date", "before": "2026-11-04 23:59", "after": "2026-11-03 23:59"}],
                "project_fields": {
                    "code": "XM2026262", "name": "氯雷他定口服溶液方法开发",
                    "client_name": "深圳海王医药科技研究院有限公司", "estimated_hours": 39,
                },
            },
        })

        sheet = load_workbook(export_audit_logs([record]))["操作日志"]
        detail = sheet["H2"].value

        self.assertIn("项目结束时间：2026-11-04 23:59 → 2026-11-03 23:59", detail)
        self.assertNotIn("end_date", detail)
        self.assertNotIn("field", detail)
        self.assertNotIn("before", detail)
        self.assertNotIn("after", detail)
        self.assertNotIn("[object Object]", detail)

    def test_exports_nested_business_detail_readably(self):
        record = present_audit_record({
            "created_at": datetime(2026, 9, 10, 16, 40),
            "user_name": "admin", "action": "project_plan_drafts_committed",
            "target_type": "project", "target_id": 262,
            "detail": {"task_details": [{"task_id": 5, "name": "方法开发", "estimated_hours": 16}]},
        })

        sheet = load_workbook(export_audit_logs([record]))["操作日志"]
        detail = sheet["H2"].value

        self.assertIn("任务详情", detail)
        self.assertIn("任务名称：方法开发", detail)
        self.assertIn("预计工时：16", detail)
        self.assertNotIn("task_id", detail)


if __name__ == "__main__":
    unittest.main()

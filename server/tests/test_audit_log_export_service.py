import unittest
from datetime import datetime

from openpyxl import load_workbook

from app.services.audit_log_export_service import export_audit_logs


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


if __name__ == "__main__":
    unittest.main()

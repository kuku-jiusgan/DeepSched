import unittest

from app.services.audit_log_presentation_service import present_audit_record


class AuditLogPresentationServiceTest(unittest.TestCase):
    def test_presents_structured_task_change(self):
        record = present_audit_record({
            "action": "task_updated",
            "target_type": "task",
            "target_id": 12,
            "detail": {
                "category": "task",
                "summary": "修改任务【P001 · 方法开发】：预计工时 8 → 12",
                "target_display": "P001 · 方法开发",
                "result": "success",
                "changes": [{"field": "预计工时", "before": 8, "after": 12}],
            },
        })

        self.assertEqual("任务管理", record["category_label"])
        self.assertEqual("修改任务", record["action_label"])
        self.assertEqual("P001 · 方法开发", record["target_display"])
        self.assertEqual(1, len(record["changes"]))

    def test_keeps_legacy_http_log_concise(self):
        record = present_audit_record({
            "action": "HTTP DELETE",
            "target_type": "api_request",
            "target_id": None,
            "detail": {"path": "/api/v1/projects/tasks/90", "success": True},
        })

        self.assertEqual("task", record["category"])
        # 8975ecb 给标签表补了 task_deleted，旧文案「删除项目任务」随之变化。
        self.assertEqual("删除任务【系统操作】 · 成功", record["summary"])


if __name__ == "__main__":
    unittest.main()

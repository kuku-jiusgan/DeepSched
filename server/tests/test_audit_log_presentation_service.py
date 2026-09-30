import copy
import unittest

from app.services.audit_log_presentation_service import present_audit_record


class AuditLogPresentationServiceTest(unittest.TestCase):
    def test_presents_structured_task_change(self):
        record = present_audit_record({
            "action": "task_updated", "target_type": "task", "target_id": 12,
            "detail": {
                "category": "task",
                "summary": "修改任务【P001 · 方法开发】：预计工时 8 → 12",
                "target_display": "P001 · 方法开发", "result": "success",
                "changes": [{"field": "预计工时", "before": 8, "after": 12}],
            },
        })

        self.assertEqual("任务管理", record["category_label"])
        self.assertEqual("修改任务", record["action_label"])
        self.assertEqual("P001 · 方法开发", record["target_display"])
        self.assertEqual([{"field": "预计工时", "before": 8, "after": 12}], record["changes"])

    def test_keeps_legacy_http_log_concise(self):
        record = present_audit_record({
            "action": "HTTP DELETE", "target_type": "api_request", "target_id": None,
            "detail": {"path": "/api/v1/projects/tasks/90", "success": True},
        })

        self.assertEqual("task", record["category"])
        self.assertEqual("删除任务【系统操作】 · 成功", record["summary"])

    def test_translates_complete_project_detail(self):
        detail = {
            "target_display": "XM2026262 · 氯雷他定口服溶液中N-亚硝基地氯雷他定方法开发",
            "project_fields": {
                "code": "XM2026262",
                "name": "氯雷他定口服溶液中N-亚硝基地氯雷他定方法开发",
                "end_date": "2026-11-03 23:59", "priority": 3,
                "start_date": "2026-09-10 00:00", "client_name": "深圳海王医药科技研究院有限公司",
                "manager_name": "李伟", "estimated_hours": 39,
            },
        }
        original = copy.deepcopy(detail)

        record = present_audit_record({
            "action": "project_updated", "target_type": "project", "target_id": 262,
            "detail": detail,
        })

        self.assertEqual({
            "项目编号": "XM2026262",
            "项目名称": "氯雷他定口服溶液中N-亚硝基地氯雷他定方法开发",
            "项目结束时间": "2026-11-03 23:59", "优先级": 3,
            "项目开始时间": "2026-09-10 00:00", "客户名称": "深圳海王医药科技研究院有限公司",
            "项目负责人": "李伟", "预计总工时": 39,
        }, record["business_detail"]["项目详情"])
        self.assertEqual(original, detail)

    def test_recursively_translates_object_arrays_and_hides_database_ids(self):
        record = present_audit_record({
            "action": "project_plan_drafts_committed", "target_type": "project", "target_id": 9,
            "detail": {
                "created": 1, "project_id": 9,
                "task_details": [{
                    "task_id": 81, "name": "方法开发", "task_type": "方法开发",
                    "estimated_hours": 16, "assignee": "李伟", "instruments": ["液相色谱仪"],
                    "predecessors": [], "parent": None,
                }],
            },
        })

        self.assertNotIn("project_id", str(record["business_detail"]))
        self.assertNotIn("task_id", str(record["business_detail"]))
        task = record["business_detail"]["任务详情"][0]
        self.assertEqual("方法开发", task["任务名称"])
        self.assertEqual(16, task["预计工时"])
        self.assertEqual("李伟", task["负责人"])

    def test_unknown_fields_are_safe_and_never_overwrite_each_other(self):
        record = present_audit_record({
            "action": "custom_action", "target_type": "project", "target_id": 3,
            "detail": {"unmapped_alpha": 0, "unmapped_beta": False},
        })

        self.assertEqual({"其他字段": 0, "其他字段（2）": False}, record["business_detail"])

    def test_translates_english_change_fields_and_nested_values(self):
        record = present_audit_record({
            "action": "project_updated", "target_type": "project", "target_id": 3,
            "detail": {"changes": [
                {"field": "estimated_hours", "before": 20, "after": 39},
                {"field": "项目结束时间", "before": {"day_type": "workday"}, "after": {"day_type": "holiday"}},
            ]},
        })

        self.assertEqual("预计总工时", record["changes"][0]["field"])
        self.assertEqual("项目结束时间", record["changes"][1]["field"])
        self.assertEqual({"日期类型": "工作日"}, record["changes"][1]["before"])
        self.assertEqual({"日期类型": "节假日"}, record["changes"][1]["after"])

    def test_translates_stable_task_type_and_hides_unresolved_entity_id(self):
        record = present_audit_record({
            "action": "task_deleted", "target_type": "task", "target_id": 12,
            "detail": {"previous_task_id": 99, "snapshot": {"name": "标准计划", "task_type": "group"}},
        })

        self.assertNotIn("前序任务", record["business_detail"])
        self.assertEqual("任务组", record["business_detail"]["任务快照"]["任务类型"])

    def test_separates_failure_reason_and_does_not_expose_raw_target_id(self):
        record = present_audit_record({
            "action": "task_paused", "target_type": "task", "target_id": 12,
            "detail": {"result": "failed", "reason": "任务正在处理中"},
        })

        self.assertEqual("任务正在处理中", record["failure_reason"])
        self.assertNotIn("原因", record["business_detail"])
        self.assertEqual("任务", record["target_display"])
        self.assertNotIn("#12", record["summary"])


if __name__ == "__main__":
    unittest.main()

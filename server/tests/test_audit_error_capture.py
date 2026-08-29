"""失败请求要把当时的错误信息记进审计日志。

此前只记路径和状态码，技术员反馈"操作失败"时，翻日志只能看到 409，看不出到底
报了什么，无从查起。
"""

import json
import unittest

from starlette.responses import JSONResponse

from app.core.logging_middleware import _capture_error_message
from app.services.audit_log_presentation_service import present_audit_record


class _FakeStream:
    """模拟 call_next 返回的流式响应：响应体读过一次就没了。"""

    def __init__(self, response: JSONResponse):
        self.status_code = response.status_code
        self.headers = response.headers
        self.media_type = response.media_type
        self._chunks = [response.body]

    @property
    def body_iterator(self):
        async def iterator():
            for chunk in self._chunks:
                yield chunk
            self._chunks = []
        return iterator()


class CaptureErrorMessageTest(unittest.IsolatedAsyncioTestCase):
    async def _capture(self, payload, status=409, media_type="application/json"):
        response = JSONResponse(status_code=status, content=payload, media_type=media_type)
        return await _capture_error_message(_FakeStream(response))

    async def test_detail_string_is_captured(self):
        rebuilt, message = await self._capture({"detail": "排程冲突，请重新计算影响"})

        self.assertEqual("排程冲突，请重新计算影响", message)
        self.assertEqual(409, rebuilt.status_code)

    async def test_response_body_survives_the_read(self):
        """读完必须原样重建，否则调用方拿到的是空响应。"""
        rebuilt, _message = await self._capture({"detail": "冲突"})

        self.assertEqual({"detail": "冲突"}, json.loads(rebuilt.body))

    async def test_structured_detail_uses_its_message(self):
        rebuilt, message = await self._capture(
            {"detail": {"message": "仪器工时不足", "schedule_failure": {"kind": "x"}}},
        )

        self.assertEqual("仪器工时不足", message)
        self.assertEqual(409, rebuilt.status_code)

    async def test_long_message_is_truncated(self):
        _rebuilt, message = await self._capture({"detail": "错" * 800})

        self.assertEqual(500, len(message))

    async def test_non_json_response_is_left_alone(self):
        response = JSONResponse(status_code=500, content={"detail": "x"})
        response.headers["content-type"] = "text/plain"

        _rebuilt, message = await _capture_error_message(_FakeStream(response))

        self.assertIsNone(message)

    async def test_missing_detail_yields_no_message(self):
        _rebuilt, message = await self._capture({"code": 1})

        self.assertIsNone(message)


class ErrorShownInTechnicalDetailTest(unittest.TestCase):
    def test_error_is_listed_first_in_technical_detail(self):
        record = present_audit_record({
            "action": "HTTP POST",
            "target_type": "api_request",
            "target_id": None,
            "detail": {
                "path": "/api/v1/detection-tasks/88/confirm-insert",
                "status": 409, "success": False, "duration_ms": 1896,
                "client_ip": "60.210.102.106", "error": "计划或排程数据已变化，请重新计算影响",
            },
        })

        technical = record["technical_detail"]
        self.assertEqual("计划或排程数据已变化，请重新计算影响", technical["错误信息"])
        self.assertEqual("错误信息", next(iter(technical)))
        # 错误信息只出现在技术信息里，不重复混进业务字段
        self.assertNotIn("error", record["business_detail"])


if __name__ == "__main__":
    unittest.main()

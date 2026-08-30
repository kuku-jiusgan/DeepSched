"""企业微信接口一律直连，不受环境变量里的代理影响。

urlopen 默认会读 http_proxy/https_proxy。一旦服务是从带代理的终端里启动的，
出口 IP 就变成代理节点的地址，企业微信按「企业可信IP」白名单校验时返回 60020
（not allow to access from your ip）；而且节点一换 IP 就变，白名单加不过来。
"""

import unittest
import urllib.request
from unittest.mock import patch

from app.services import wecom_auth_service


class WeComDirectConnectionTest(unittest.TestCase):
    def test_opener_carries_no_proxy_handler(self):
        """build_opener(ProxyHandler({})) 会顶掉默认那个按环境变量建代理的
        ProxyHandler，且自身因为没有代理而不注册任何 *_open，最终链上一个代理
        处理器都不剩——请求因此必定直连。"""
        names = [type(handler).__name__ for handler in wecom_auth_service._DIRECT_OPENER.handlers]

        self.assertNotIn("ProxyHandler", names)
        self.assertIn("HTTPSHandler", names)

    def test_default_opener_would_have_used_the_proxy(self):
        """对照：默认 opener 在有代理变量时确实会装上 ProxyHandler。"""
        with patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:7897"}, clear=False):
            names = [
                type(handler).__name__
                for handler in urllib.request.build_opener().handlers
            ]

        self.assertIn("ProxyHandler", names)

    def test_requests_go_through_the_direct_opener(self):
        """必须用这个 opener，不能用 urlopen——后者走的是全局 opener。"""
        with patch.object(wecom_auth_service._DIRECT_OPENER, "open") as opener:
            opener.return_value.__enter__.return_value.read.return_value = b'{"errcode": 0}'

            result = wecom_auth_service._get_json("https://qyapi.weixin.qq.com/x")

        opener.assert_called_once()
        self.assertEqual({"errcode": 0}, result)


if __name__ == "__main__":
    unittest.main()

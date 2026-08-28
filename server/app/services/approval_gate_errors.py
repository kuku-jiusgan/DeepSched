"""方案签批领域异常。独立成模块，供签批相关的各个 service 共享。"""

from __future__ import annotations


class ApprovalGateNotFoundError(Exception):
    pass

class ApprovalGatePermissionError(Exception):
    pass

class ApprovalGateInvalidError(Exception):
    pass

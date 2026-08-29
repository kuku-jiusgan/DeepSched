import time, json
from datetime import datetime

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.logger import logger
from app.core.database import SessionLocal
from app.services.auth_session_service import session_username
from app.services.audit_log_service import has_business_audit_since, record_audit_log

AUDIT_IGNORED_PATHS = {"/api/v1/users/keep-alive"}
LOGIN_PATHS = {"/api/v1/users/login", "/api/v1/wecom-auth/login"}

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        started_at = datetime.now()
        path = request.scope.get("path", "")
        client_ip = request.client.host if request.client else "unknown"
        operator = "anonymous"
        token = ""
        authorization = request.headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        if token:
            db = SessionLocal()
            try:
                operator = session_username(db, token) or operator
            finally:
                db.close()

        try:
            response = await call_next(request)
            status = response.status_code
            duration_ms = int((time.time() - start) * 1000)
            success = status < 400
            
            if path != "/api/v1/health":
                logger.log(
                    operator=operator,
                    action=f"{request.method} {path}",
                    target=path,
                    success=success,
                    detail=f"HTTP {status} ({duration_ms}ms)",
                    method=request.method,
                    path=path,
                    client_ip=client_ip,
                )
                should_record_audit = (
                    request.method in {"POST", "PUT", "PATCH", "DELETE"}
                    and path not in AUDIT_IGNORED_PATHS
                    and not (success and path in LOGIN_PATHS)
                )
                if should_record_audit:
                    error_message = None
                    if not success:
                        response, error_message = await _capture_error_message(response)
                    db = SessionLocal()
                    try:
                        if not has_business_audit_since(db, operator, started_at):
                            audit_detail = {
                                "path": path, "status": status, "success": success,
                                "client_ip": client_ip, "duration_ms": duration_ms,
                            }
                            if error_message:
                                audit_detail["error"] = error_message
                            record_audit_log(
                                db, operator, f"HTTP {request.method}", "api_request", None,
                                audit_detail,
                            )
                            db.commit()
                    finally:
                        db.close()
            return response
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            if path != "/api/v1/health":
                logger.log(
                    operator=operator,
                    action=f"{request.method} {path}",
                    target=path,
                    success=False,
                    detail=str(e)[:200],
                    method=request.method,
                    path=path,
                    client_ip=client_ip,
                )
            raise


async def _capture_error_message(response) -> tuple[Response, str | None]:
    """读出失败响应里的错误信息，并把响应重新组装回去。

    审计日志此前只记路径和状态码，事后翻日志看不出当时到底报了什么——技术员
    反馈"操作失败"时无从查起。响应体是流，读过一次就没了，所以必须缓冲后按原状
    重建。只在请求失败且需要审计时才走这条路，正常请求不受影响。
    """
    body = b"".join([chunk async for chunk in response.body_iterator])
    rebuilt = Response(
        content=body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )
    if not response.headers.get("content-type", "").startswith("application/json"):
        return rebuilt, None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return rebuilt, None
    if not isinstance(payload, dict):
        return rebuilt, None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        detail = detail.get("message")
    if not isinstance(detail, str) or not detail.strip():
        return rebuilt, None
    return rebuilt, detail.strip()[:500]

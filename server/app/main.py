from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.database import engine, Base, SessionLocal
from app.core.config import get_settings
from app.core.schema_migrations import ensure_runtime_schema
from app.models import models
from app.services.wecom_delivery_service import (
    start_wecom_delivery_worker,
    stop_wecom_delivery_worker,
)
from app.services.calendar_service import ensure_calendar_year
from app.services.calendar_sync_worker import (
    start_calendar_sync_worker,
    stop_calendar_sync_worker,
)
from app.services.schedule_deadline_recommendation_job_service import (
    start_deadline_recommendation_worker,
    stop_deadline_recommendation_worker,
)
from app.services.dashboard_snapshot_worker import (
    start_dashboard_snapshot_worker,
    stop_dashboard_snapshot_worker,
)
from app.services.lab_status_snapshot_worker import (
    start_lab_status_snapshot_worker,
    stop_lab_status_snapshot_worker,
)
from app.services.instrument_utilization_snapshot_worker import (
    start_instrument_utilization_snapshot_worker,
    stop_instrument_utilization_snapshot_worker,
)
from app.api import protected_router, users, wecom_auth
from app.api.exception_handlers import register_domain_exception_handlers

settings = get_settings()
is_production = settings.ENVIRONMENT.lower() == "production"
if settings.AUTO_CREATE_SCHEMA:
    Base.metadata.create_all(bind=engine)
ensure_runtime_schema(engine)

app = FastAPI(
    title="资源智能调度平台",
    version="1.0.0",
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)
register_domain_exception_handlers(app)
cors_origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]


@app.on_event("startup")
def start_background_workers():
    db = SessionLocal()
    try:
        current_year = __import__('datetime').date.today().year
        for year in (current_year, current_year + 1):
            ensure_calendar_year(db, year, commit=True)
    finally:
        db.close()
    start_calendar_sync_worker()
    start_wecom_delivery_worker()
    start_deadline_recommendation_worker()
    start_dashboard_snapshot_worker()
    start_lab_status_snapshot_worker()
    start_instrument_utilization_snapshot_worker()


@app.on_event("shutdown")
def stop_background_workers():
    stop_calendar_sync_worker()
    stop_wecom_delivery_worker()
    stop_deadline_recommendation_worker()
    stop_dashboard_snapshot_worker()
    stop_lab_status_snapshot_worker()
    stop_instrument_utilization_snapshot_worker()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_json_utf8_charset(request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "请求体过大"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Content-Length 无效"})
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type == "application/json":
        response.headers["content-type"] = "application/json; charset=utf-8"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    is_api_request = request.scope.get("path", "").startswith("/api/")
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if is_api_request else
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if is_api_request:
        response.headers["Cache-Control"] = "no-store"
    elif request.scope.get("path", "").startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    return response

from app.core.logging_middleware import LoggingMiddleware
app.add_middleware(LoggingMiddleware)

app.include_router(protected_router)
app.include_router(users.router)
app.include_router(wecom_auth.router)

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

import os, glob
from app.core.database import get_db
@app.get("/api/v1/logs")
def get_logs(
    hours: int = Query(6, ge=1, le=24, description="最近多少小时的日志"),
    token: str = Depends(users.auth_token),
    db = Depends(get_db),
):
    users.require_admin(token, db)
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    files = sorted(glob.glob(os.path.join(log_dir, "operations_*.log")), reverse=True)
    entries = []
    cutoff = __import__('time').time() - hours * 3600
    for f in files[:4]:  # Check up to 4 recent files
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        entry = __import__('json').loads(line.strip())
                        ts = __import__('datetime').datetime.fromisoformat(entry["timestamp"])
                        if ts.timestamp() >= cutoff:
                            entries.append(entry)
                    except:
                        pass
        except:
            pass
    return {"count": len(entries), "entries": entries[-500:]}  # Return last 500

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.services.wecom_auth_service import (
    WeComAccountBindingError,
    WeComAuthenticationError,
    WeComConfigError,
    build_authorize_url,
    build_scan_authorize_url,
    login_with_wecom,
)


router = APIRouter(prefix="/api/v1/wecom-auth", tags=["wecom-auth"])


class WeComLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=1, max_length=512)


@router.get("/authorize-url")
def authorize_url(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    callback_url = settings.WECOM_OAUTH_CALLBACK_URL or f"{str(request.base_url).rstrip('/')}/login"
    try:
        return {"authorize_url": build_authorize_url(db, callback_url, settings.WECOM_OAUTH_STATE_EXPIRE_SECONDS)}
    except WeComConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/scan-authorize-url")
def scan_authorize_url(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    callback_url = settings.WECOM_OAUTH_CALLBACK_URL or f"{str(request.base_url).rstrip('/')}/login"
    try:
        return {"authorize_url": build_scan_authorize_url(
            db, callback_url, settings.WECOM_OAUTH_STATE_EXPIRE_SECONDS,
        )}
    except WeComConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/login")
def wecom_login(data: WeComLoginRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        return login_with_wecom(db, data.code, data.state, settings.WECOM_OAUTH_STATE_EXPIRE_SECONDS, 3 * 60 * 60)
    except WeComConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except WeComAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except WeComAccountBindingError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

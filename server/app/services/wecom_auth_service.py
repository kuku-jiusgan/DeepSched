from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from app.models import PushChannelConfig, User, WeComOAuthState
from app.services.auth_session_service import create_session
from app.services.audit_log_service import record_audit_log
from app.services.user_role_service import user_roles


AUTHORIZE_URL = "https://open.weixin.qq.com/connect/oauth2/authorize"
SCAN_AUTHORIZE_URL = "https://open.work.weixin.qq.com/wwopen/sso/qrConnect"
GET_USER_INFO_URL = "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo"


class WeComConfigError(Exception):
    pass


class WeComAuthenticationError(Exception):
    pass


class WeComAccountBindingError(Exception):
    pass


def build_authorize_url(db, callback_url: str, state_expire_seconds: int) -> str:
    config = _load_config(db)
    state = _create_state(config.wecom_secret, state_expire_seconds)
    _store_state(db, state, state_expire_seconds)
    query = urllib.parse.urlencode({
        "appid": config.wecom_corp_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "snsapi_base",
        "state": state,
        "agentid": config.wecom_agent_id,
    })
    return f"{AUTHORIZE_URL}?{query}#wechat_redirect"


def build_scan_authorize_url(db, callback_url: str, state_expire_seconds: int) -> str:
    """Build the browser QR-login URL; client OAuth remains unchanged."""
    config = _load_config(db)
    state = _create_state(config.wecom_secret, state_expire_seconds)
    _store_state(db, state, state_expire_seconds)
    query = urllib.parse.urlencode({
        "appid": config.wecom_corp_id,
        "agentid": config.wecom_agent_id,
        "redirect_uri": callback_url,
        "state": state,
    })
    return f"{SCAN_AUTHORIZE_URL}?{query}"


def login_with_wecom(db, code: str, state: str, state_expire_seconds: int, idle_timeout_seconds: int) -> dict:
    config = _load_config(db)
    _verify_state(state, config.wecom_secret, state_expire_seconds)
    _consume_state(db, state)
    wecom_user_id = _exchange_user_id(config, code)
    users = db.query(User).filter(User.wecom_id == wecom_user_id).all()
    if not users:
        raise WeComAccountBindingError(f"企业微信账号 {wecom_user_id} 尚未绑定系统用户，请联系管理员")
    if len(users) > 1:
        raise WeComAccountBindingError("该企业微信账号绑定了多个系统用户，请联系管理员处理")
    user = users[0]
    if not user.is_active:
        raise WeComAccountBindingError("对应的系统账号已停用，请联系管理员")
    token = create_session(db, user.id, user.username, idle_timeout_seconds)
    record_audit_log(
        db, user.display_name or user.username, "user_logged_in", "user", user.id,
        {
            "target_display": f"{user.display_name}（{user.username}）",
            "username": user.username,
            "display_name": user.display_name,
            "login_method": "企业微信",
        },
    )
    db.commit()
    return {
        "token": token,
        "expires_in": idle_timeout_seconds,
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "roles": user_roles(user),
        },
    }


def _load_config(db) -> PushChannelConfig:
    config = db.query(PushChannelConfig).order_by(PushChannelConfig.id).first()
    if not config or not config.wecom_enabled:
        raise WeComConfigError("企业微信登录尚未启用")
    config.wecom_corp_id = (config.wecom_corp_id or "").strip()
    config.wecom_agent_id = (config.wecom_agent_id or "").strip()
    config.wecom_secret = (config.wecom_secret or "").strip()
    if not config.wecom_corp_id or not config.wecom_agent_id or not config.wecom_secret:
        raise WeComConfigError("企业微信 CorpID、AgentId、Secret 配置不完整")
    return config


def _create_state(secret: str, _expire_seconds: int) -> str:
    payload = f"{int(datetime.now().timestamp())}.{secrets.token_urlsafe(18)}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _verify_state(state: str, secret: str, expire_seconds: int) -> None:
    try:
        timestamp_text, nonce, signature = state.split(".", 2)
        timestamp = int(timestamp_text)
    except (AttributeError, TypeError, ValueError):
        raise WeComAuthenticationError("企业微信登录状态无效，请重新进入应用")
    payload = f"{timestamp_text}.{nonce}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    age = int(datetime.now().timestamp()) - timestamp
    if not hmac.compare_digest(signature, expected) or age < 0 or age > expire_seconds:
        raise WeComAuthenticationError("企业微信登录状态已失效，请重新进入应用")


def _store_state(db, state: str, expire_seconds: int) -> None:
    now = datetime.now()
    db.query(WeComOAuthState).filter(WeComOAuthState.expires_at < now).delete()
    db.add(WeComOAuthState(
        state_hash=_state_hash(state),
        expires_at=now + timedelta(seconds=expire_seconds),
    ))
    db.commit()


def _consume_state(db, state: str) -> None:
    now = datetime.now()
    record = (
        db.query(WeComOAuthState)
        .filter(WeComOAuthState.state_hash == _state_hash(state))
        .with_for_update()
        .first()
    )
    if not record or record.used_at or record.expires_at < now:
        raise WeComAuthenticationError("企业微信登录状态已使用或已失效，请重新进入应用")
    record.used_at = now
    db.commit()


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _exchange_user_id(config: PushChannelConfig, code: str) -> str:
    token = _get_access_token(config)
    query = urllib.parse.urlencode({"access_token": token, "code": code})
    response = _get_json(f"{GET_USER_INFO_URL}?{query}")
    if response.get("errcode") != 0:
        raise WeComAuthenticationError(str(response.get("errmsg") or "企业微信身份校验失败"))
    user_id = response.get("UserId") or response.get("userid")
    if not user_id:
        raise WeComAccountBindingError("当前访问者不是该企业的已绑定成员")
    return str(user_id)


def _get_access_token(config: PushChannelConfig) -> str:
    query = urllib.parse.urlencode({"corpid": config.wecom_corp_id, "corpsecret": config.wecom_secret})
    response = _get_json(f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?{query}")
    if response.get("errcode") != 0 or not response.get("access_token"):
        raise WeComConfigError(str(response.get("errmsg") or "无法获取企业微信访问凭证"))
    return str(response["access_token"])


def _get_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WeComAuthenticationError(f"企业微信服务暂时不可用：{exc}")

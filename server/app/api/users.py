import hashlib
import hmac
import re
import secrets
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import get_settings
from app.models import User
from app.services.user_directory_service import list_user_directory
from app.services.auth_session_service import (
    check_throttle,
    clear_failure,
    create_session,
    record_failure,
    resolve_session_user,
    revoke_session,
    revoke_user_sessions,
)
from app.schemas.schemas import UserCreate, UserDirectoryOut, UserOut
from app.services.role_permission_service import action_allowed_for_roles, permissions_for_roles
from app.services.user_role_service import has_role, primary_role, user_roles
from app.services.audit_log_service import record_audit_log, user_audit_detail, user_audit_snapshot

router = APIRouter(prefix="/api/v1/users", tags=["users"])

ROLE_OPTIONS = ["系统管理员", "项目管理员", "分析所所长", "技术组长", "技术员"]
ADMIN_ROLE = "系统管理员"
IDLE_TIMEOUT_SECONDS = 3 * 60 * 60
LOGIN_FAILURE_WINDOW_SECONDS = 10 * 60
LOGIN_LOCK_SECONDS = 15 * 60
MAX_LOGIN_FAILURES = 5
MAX_LOGIN_FAILURE_KEYS = 5000
PBKDF2_ITERATIONS = 120_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-constant-login-cost")


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, digest = stored.split("$", 3)
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
            return hmac.compare_digest(candidate, digest)
        except (TypeError, ValueError):
            return False
    if ":" in stored:
        salt, digest = stored.split(":", 1)
        candidate = hashlib.sha256((salt + password).encode()).hexdigest()
        return hmac.compare_digest(candidate, digest)
    return False


def needs_password_rehash(stored: str) -> bool:
    return not stored.startswith("pbkdf2_sha256$")


def create_token(user_id: int, username: str, db: Session) -> str:
    return create_session(db, user_id, username, IDLE_TIMEOUT_SECONDS)


def auth_token(
    authorization: str | None = Header(None),
) -> str:
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value
    raise HTTPException(status_code=401, detail="未登录或登录已过期")


def get_current_user(token: str, db: Session) -> User:
    user = resolve_session_user(db, token, IDLE_TIMEOUT_SECONDS)
    if user is None:
        db.commit()
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    db.commit()
    return user


def require_authenticated_user(
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
) -> User:
    return get_current_user(token, db)


def require_admin(token: str, db: Session) -> User:
    user = get_current_user(token, db)
    if not has_role(user, ADMIN_ROLE):
        raise HTTPException(status_code=403, detail="仅系统管理员可管理用户")
    return user


def require_user_manager(token: str, db: Session, action: str | None = None) -> User:
    user = get_current_user(token, db)
    roles = user_roles(user)
    permission = next(item for item in permissions_for_roles(db, roles) if item["page_key"] == "/system/users")
    allowed = action_allowed_for_roles(db, roles, "/system/users", action) if action else permission["can_view"]
    if not allowed:
        raise HTTPException(status_code=403, detail="当前角色没有用户管理权限")
    return user


def ensure_admin_target_permission(
    operator: User,
    target: User | None,
    requested_roles: str | list[str],
) -> None:
    if has_role(operator, ADMIN_ROLE):
        return
    roles = [requested_roles] if isinstance(requested_roles, str) else requested_roles
    if ADMIN_ROLE in roles or (target and has_role(target, ADMIN_ROLE)):
        raise HTTPException(status_code=403, detail="只有系统管理员可以管理系统管理员账号")


def validate_username(username: str) -> str:
    value = username.strip()
    if not USERNAME_PATTERN.match(value):
        raise HTTPException(status_code=400, detail="用户名需为3-50位英文、数字、点、下划线或短横线")
    return value


def validate_roles(roles: list[str]) -> list[str]:
    if not roles:
        raise HTTPException(status_code=400, detail="请至少选择一个角色")
    invalid = [role for role in roles if role not in ROLE_OPTIONS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"无效角色：{invalid[0]}")
    return list(dict.fromkeys(roles))


def validate_wecom_id(db: Session, wecom_id: str | None, exclude_user_id: int | None = None) -> str | None:
    value = wecom_id.strip() if wecom_id else None
    if not value:
        return None
    query = db.query(User).filter(User.wecom_id == value)
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    if query.first():
        raise HTTPException(status_code=409, detail=f"企业微信号 {value} 已绑定其他用户")
    return value


def validate_password_strength(password: str | None) -> None:
    if not password:
        return
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    if len(password) < 8 or not has_letter or not has_digit:
        raise HTTPException(status_code=400, detail="密码至少8位，且必须包含字母和数字")


def active_admin_count(db: Session, exclude_user_id: int | None = None) -> int:
    query = db.query(User).filter(User.is_active.is_(True))
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return sum(has_role(user, ADMIN_ROLE) for user in query.all())


def ensure_admin_safety(db: Session, operator: User, target: User, data: UserCreate | None = None, deleting: bool = False) -> None:
    if operator.id == target.id and deleting:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    if data and operator.id == target.id and not data.is_active:
        raise HTTPException(status_code=400, detail="不能停用当前登录账号")
    requested_roles = (data.roles or [data.role]) if data else []
    if data and operator.id == target.id and ADMIN_ROLE not in requested_roles:
        raise HTTPException(status_code=400, detail="不能修改当前登录账号的管理员角色")
    if not has_role(target, ADMIN_ROLE):
        return
    will_remove_admin = deleting or (
        data is not None
        and (ADMIN_ROLE not in requested_roles or not data.is_active)
    )
    if will_remove_admin and active_admin_count(db, target.id) <= 0:
        raise HTTPException(status_code=400, detail="至少需要保留一个启用的系统管理员")


def login_key(request: Request, username: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"{client}:{username.lower()}"


def check_login_throttle(request: Request, username: str, db: Session) -> None:
    if check_throttle(db, login_key(request, username)):
        raise HTTPException(status_code=429, detail="登录失败次数过多，请15分钟后再试")


def record_login_failure(request: Request, username: str, db: Session) -> None:
    record_failure(
        db,
        login_key(request, username),
        LOGIN_FAILURE_WINDOW_SECONDS,
        LOGIN_LOCK_SECONDS,
        MAX_LOGIN_FAILURES,
        MAX_LOGIN_FAILURE_KEYS,
    )
    db.commit()


def clear_login_failure(request: Request, username: str, db: Session) -> None:
    clear_failure(db, login_key(request, username))


def _seed_admin(db: Session):
    if db.query(User).filter(User.username == "admin").first():
        return
    initial_password = get_settings().INITIAL_ADMIN_PASSWORD
    if not initial_password:
        return
    validate_password_strength(initial_password)
    db.add(
        User(
            username="admin",
            display_name="系统管理员",
            role=ADMIN_ROLE,
            roles=[ADMIN_ROLE],
            password_hash=hash_password(initial_password),
            is_active=True,
        )
    )
    db.commit()


@router.get("/directory", response_model=List[UserDirectoryOut])
def get_user_directory(
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    get_current_user(token, db)
    return list_user_directory(db)


@router.get("", response_model=List[UserOut])
def list_users(token: str = Depends(auth_token), db: Session = Depends(get_db)):
    _seed_admin(db)
    require_user_manager(token, db)
    return db.query(User).order_by(User.id).all()


@router.post("", response_model=UserOut)
def create_user(data: UserCreate, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    operator = require_user_manager(token, db, action="create")
    roles = validate_roles(data.roles or [data.role])
    data.role = primary_role(roles)
    data.roles = roles
    ensure_admin_target_permission(operator, None, roles)
    username = validate_username(data.username)
    validate_password_strength(data.password)
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail=f"用户名 {username} 已存在")
    if data.role not in ROLE_OPTIONS:
        raise HTTPException(status_code=400, detail=f"无效角色：{data.role}")
    if not data.password:
        raise HTTPException(status_code=400, detail="请设置登录密码")
    payload = data.model_dump()
    payload["username"] = username
    payload["wecom_id"] = validate_wecom_id(db, data.wecom_id)
    password = payload.pop("password")
    user = User(**payload, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    record_audit_log(
        db, operator.display_name or operator.username, "user_created", "user", user.id,
        user_audit_detail(user),
    )
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: UserCreate, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    operator = require_user_manager(token, db, action="edit")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    roles = validate_roles(data.roles or [data.role])
    data.role = primary_role(roles)
    data.roles = roles
    ensure_admin_target_permission(operator, user, roles)
    username = validate_username(data.username)
    validate_password_strength(data.password)
    existing = db.query(User).filter(User.username == username, User.id != user_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"用户名 {username} 已被其他用户使用")
    if data.role not in ROLE_OPTIONS:
        raise HTTPException(status_code=400, detail=f"无效角色：{data.role}")
    ensure_admin_safety(db, operator, user, data)
    before = user_audit_snapshot(user)
    payload = data.model_dump()
    payload["username"] = username
    payload["wecom_id"] = validate_wecom_id(db, data.wecom_id, user_id)
    password = payload.pop("password", None)
    for key, val in payload.items():
        setattr(user, key, val)
    if password:
        user.password_hash = hash_password(password)
    record_audit_log(
        db, operator.display_name or operator.username, "user_updated", "user", user.id,
        user_audit_detail(user, before),
    )
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}")
def delete_user(user_id: int, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    operator = require_user_manager(token, db, action="delete")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    ensure_admin_target_permission(operator, user, user.role)
    ensure_admin_safety(db, operator, user, deleting=True)
    detail = user_audit_detail(user)
    record_audit_log(
        db, operator.display_name or operator.username, "user_deleted", "user", user.id, detail,
    )
    db.delete(user)
    db.commit()
    return {"detail": "已删除"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class ChangeMyPasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class ResetUserPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.put("/{user_id}/password")
def reset_user_password(
    user_id: int,
    data: ResetUserPasswordRequest,
    token: str = Depends(auth_token),
    db: Session = Depends(get_db),
):
    operator = require_user_manager(token, db, action="password")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    ensure_admin_target_permission(operator, user, user.role)
    validate_password_strength(data.password)
    user.password_hash = hash_password(data.password)
    revoke_user_sessions(db, user.id)
    record_audit_log(
        db, operator.display_name or operator.username, "user_password_reset", "user", user.id,
        user_audit_detail(user),
    )
    db.commit()
    return {"detail": "密码已修改"}


@router.post("/login")
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    _seed_admin(db)
    username = data.username.strip()
    check_login_throttle(request, username, db)
    user = db.query(User).filter(User.username == username).first()
    stored_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    password_ok = verify_password(data.password, stored_hash or DUMMY_PASSWORD_HASH)
    if not user or not password_ok:
        record_login_failure(request, username, db)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        record_login_failure(request, username, db)
        raise HTTPException(status_code=403, detail="账号已停用")
    if needs_password_rehash(user.password_hash or ""):
        user.password_hash = hash_password(data.password)
        db.commit()
    clear_login_failure(request, username, db)
    token = create_token(user.id, user.username, db)
    record_audit_log(
        db, user.display_name or user.username, "user_logged_in", "user", user.id,
        {
            "target_display": f"{user.display_name}（{user.username}）",
            "username": user.username,
            "display_name": user.display_name,
            "login_method": "账号密码",
        },
    )
    db.commit()
    return {
        "token": token,
        "expires_in": IDLE_TIMEOUT_SECONDS,
        "user": {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role, "roles": user_roles(user)},
    }


@router.get("/me")
def get_me(token: str = Depends(auth_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role, "roles": user_roles(user)}


@router.post("/me/password")
def change_my_password(data: ChangeMyPasswordRequest, token: str = Depends(auth_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if not verify_password(data.old_password, user.password_hash or ""):
        raise HTTPException(status_code=400, detail="原密码不正确")
    validate_password_strength(data.new_password)
    if data.old_password == data.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    user.password_hash = hash_password(data.new_password)
    revoke_user_sessions(db, user.id)
    db.commit()
    return {"detail": "密码已修改，请重新登录"}


@router.post("/keep-alive")
def keep_alive(token: str = Depends(auth_token), db: Session = Depends(get_db)):
    get_current_user(token, db)
    return {"detail": "会话已续期", "expires_in": IDLE_TIMEOUT_SECONDS}


@router.post("/logout")
def logout(token: str = Depends(auth_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    record_audit_log(
        db, user.display_name or user.username, "user_logged_out", "user", user.id,
        {
            "target_display": f"{user.display_name}（{user.username}）",
            "username": user.username,
            "display_name": user.display_name,
            "logout_method": "主动退出",
        },
    )
    revoke_session(db, token)
    db.commit()
    return {"detail": "已退出"}

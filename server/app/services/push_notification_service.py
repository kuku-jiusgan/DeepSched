from __future__ import annotations

import json
from typing import Iterable

from app.models import AlertRule, Notification, PushChannelConfig, User
from app.services.wecom_delivery_service import (
    enqueue_wecom_delivery,
    reset_wecom_token_cache,
)
from app.services.user_role_service import user_roles


CONFIRM_REQUIRED_TYPES = {"instrument_fault_schedule_conflict"}
WECOM_CONFIG_FIELDS = {"wecom_corp_id", "wecom_agent_id", "wecom_secret"}


def get_push_config(db) -> PushChannelConfig:
    config = db.query(PushChannelConfig).order_by(PushChannelConfig.id).first()
    if config:
        if not config.wecom_enabled:
            config.wecom_enabled = True
            db.commit()
            db.refresh(config)
        return config
    config = PushChannelConfig(wecom_enabled=True)
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def update_push_config(db, data: dict) -> PushChannelConfig:
    config = get_push_config(db)
    for field, value in data.items():
        if field in WECOM_CONFIG_FIELDS and isinstance(value, str):
            value = value.strip() or None
        setattr(config, field, value)
    config.wecom_enabled = True
    db.commit()
    db.refresh(config)
    reset_wecom_token_cache()
    return config


def push_by_rule(
    db,
    rule_type: str,
    users: Iterable[User],
    title: str,
    content: str,
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
    context_roles: Iterable[str] | None = None,
    external_delivery: bool = True,
) -> int:
    rule = db.query(AlertRule).filter(AlertRule.rule_type == rule_type).first()
    if rule and not rule.enabled:
        return 0

    unique_users = _notification_recipients(rule, users, context_roles)
    sent_count = 0

    for user in unique_users:
        db.add(_notification(
            user=user,
            rule_type=rule_type,
            title=title,
            content=content,
            channel="site",
            delivery_status="success",
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        ))
        sent_count += 1

        if external_delivery:
            db.add(_notification(
                user=user,
                rule_type=rule_type,
                title=title,
                content=content,
                channel="wecom",
                delivery_status="pending",
                related_entity_type=related_entity_type,
                related_entity_id=related_entity_id,
            ))

    if unique_users and external_delivery:
        enqueue_wecom_delivery()

    return sent_count


def _notification_recipients(
    rule: AlertRule | None,
    users: Iterable[User],
    context_roles: Iterable[str] | None,
) -> list[User]:
    active_users = _unique_active_users(users)
    if not rule or rule.notify_roles is None:
        return active_users
    notify_roles = _parse_notify_roles(rule.notify_roles)
    matched_context_roles = notify_roles & set(context_roles or [])
    return [
        user for user in active_users
        if set(user_roles(user)) & notify_roles or matched_context_roles
    ]


def _parse_notify_roles(value: str) -> set[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {role for role in parsed if isinstance(role, str) and role}


def _unique_active_users(users: Iterable[User]) -> list[User]:
    result: list[User] = []
    seen = set()
    for user in users:
        if not user or not user.username or not user.is_active or user.username in seen:
            continue
        seen.add(user.username)
        result.append(user)
    return result


def _notification(
    user: User,
    rule_type: str,
    title: str,
    content: str,
    channel: str,
    delivery_status: str,
    related_entity_type: str | None,
    related_entity_id: int | None,
    error_message: str | None = None,
) -> Notification:
    return Notification(
        user_name=user.username,
        n_type=rule_type,
        channel=channel,
        delivery_status=delivery_status,
        error_message=error_message,
        title=title,
        content=content,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        is_confirmed=False if rule_type in CONFIRM_REQUIRED_TYPES else None,
    )

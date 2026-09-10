from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.access import require_management_user
from app.core.database import get_db
from app.schemas.alert_rule_schemas import (
    AlertRuleOut,
    AlertRuleUpdate,
    PushChannelConfigOut,
    PushChannelConfigUpdate,
)
from app.services.alert_rule_service import list_alert_rules, update_alert_rule
from app.services.push_notification_service import get_push_config, update_push_config


router = APIRouter(
    prefix="/api/v1/alert-rules",
    tags=["alert-rules"],
    dependencies=[Depends(require_management_user)],
)


@router.get("", response_model=list[AlertRuleOut])
def list_rules(db: Session = Depends(get_db)):
    return list_alert_rules(db)


@router.get("/push-config", response_model=PushChannelConfigOut)
def get_config(db: Session = Depends(get_db)):
    return _push_config_response(get_push_config(db))


@router.put("/push-config", response_model=PushChannelConfigOut)
def save_config(data: PushChannelConfigUpdate, db: Session = Depends(get_db)):
    config = update_push_config(db, data.model_dump(exclude_unset=True))
    return _push_config_response(config)


@router.put("/{rule_id}", response_model=AlertRuleOut)
def update_rule(rule_id: int, data: AlertRuleUpdate, db: Session = Depends(get_db)):
    return update_alert_rule(db, rule_id, data.model_dump(exclude_unset=True, exclude_none=True))


def _push_config_response(config) -> PushChannelConfigOut:
    return PushChannelConfigOut(
        id=config.id,
        wecom_enabled=config.wecom_enabled,
        wecom_corp_id=config.wecom_corp_id,
        wecom_agent_id=config.wecom_agent_id,
        has_wecom_secret=bool(config.wecom_secret),
    )

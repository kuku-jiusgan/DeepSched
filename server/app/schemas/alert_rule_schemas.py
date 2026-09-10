from pydantic import BaseModel


class AlertRuleOut(BaseModel):
    id: int
    name: str
    rule_type: str
    enabled: bool
    enable_site: bool = True
    enable_wecom: bool = True
    notify_roles: str | None = None
    threshold_minutes: int = 0
    threshold_percent: int = 0
    model_config = {"from_attributes": True}


class AlertRuleUpdate(BaseModel):
    enabled: bool | None = None
    enable_site: bool | None = None
    enable_wecom: bool | None = None
    notify_roles: str | None = None
    threshold_minutes: int | None = None
    threshold_percent: int | None = None


class PushChannelConfigOut(BaseModel):
    id: int
    wecom_enabled: bool = False
    wecom_corp_id: str | None = None
    wecom_agent_id: str | None = None
    has_wecom_secret: bool = False
    model_config = {"from_attributes": True}


class PushChannelConfigUpdate(BaseModel):
    wecom_enabled: bool | None = None
    wecom_corp_id: str | None = None
    wecom_agent_id: str | None = None
    wecom_secret: str | None = None

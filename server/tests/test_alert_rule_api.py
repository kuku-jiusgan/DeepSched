import json
import socket
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.access import require_management_user
from app.api.alert_rules import router
from app.api.exception_handlers import register_domain_exception_handlers
from app.core.database import get_db
from app.models import AlertRule
from app.services.alert_rule_service import DEFAULT_ALERT_RULES


class LocalClient:
    def __init__(self, port):
        self.base_url = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, payload=None):
        request = Request(
            self.base_url + path, method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            body = json.loads(response.read())
            return SimpleNamespace(status_code=response.status, json=lambda: body)

    def get(self, path):
        return self.request("GET", path)

    def put(self, path, json):
        return self.request("PUT", path, json)


@pytest.fixture
def context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    AlertRule.__table__.create(engine)
    with Session(engine) as db:
        rule = AlertRule(
            name="排程变更通知", rule_type="schedule_changed", enabled=False,
            enable_site=False, enable_wecom=False, notify_roles='["技术员"]',
            threshold_minutes=35, threshold_percent=130,
        )
        db.add(rule)
        db.commit()
        rule_id = rule.id
        app = FastAPI()
        app.include_router(router)
        register_domain_exception_handlers(app)

        def session():
            yield db

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[require_management_user] = lambda: None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert server.started, "Isolated API test server failed to start"
                yield LocalClient(listener.getsockname()[1]), db, rule_id
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                assert not thread.is_alive(), "Isolated API test server failed to stop"
    engine.dispose()


@pytest.mark.parametrize("site,wecom", [(False, False), (False, True), (True, False), (True, True)])
def test_reading_rules_preserves_existing_settings(context, site, wecom):
    client, db, rule_id = context
    rule = db.get(AlertRule, rule_id)
    rule.enable_site, rule.enable_wecom = site, wecom
    db.commit()

    for _ in range(2):
        response = client.get("/api/v1/alert-rules")
        assert response.status_code == 200
        rows = response.json()
        saved = next(row for row in rows if row["id"] == rule_id)
        assert (saved["enable_site"], saved["enable_wecom"]) == (site, wecom)
        assert saved["enabled"] is False
        assert saved["notify_roles"] == '["技术员"]'
        assert saved["threshold_minutes"] == 35
        assert saved["threshold_percent"] == 130
        assert len(rows) == len(DEFAULT_ALERT_RULES)
        assert all(row["enable_site"] and row["enable_wecom"] for row in rows if row["id"] != rule_id)
    db.expire_all()
    rule = db.get(AlertRule, rule_id)
    assert (rule.enable_site, rule.enable_wecom) == (site, wecom)


@pytest.mark.parametrize("site,wecom", [(False, False), (False, True), (True, False), (True, True)])
def test_saving_channels_preserves_values_on_next_read(context, site, wecom):
    client, db, rule_id = context
    rule = db.get(AlertRule, rule_id)
    rule.enable_site, rule.enable_wecom = not site, not wecom
    db.commit()

    response = client.put(f"/api/v1/alert-rules/{rule_id}", json={
        "enable_site": site, "enable_wecom": wecom,
    })
    assert response.status_code == 200
    assert (response.json()["enable_site"], response.json()["enable_wecom"]) == (site, wecom)
    saved = next(row for row in client.get("/api/v1/alert-rules").json() if row["id"] == rule_id)
    assert (saved["enable_site"], saved["enable_wecom"]) == (site, wecom)
    assert saved["enabled"] is False
    db.expire_all()
    rule = db.get(AlertRule, rule_id)
    assert (rule.enable_site, rule.enable_wecom) == (site, wecom)


@pytest.mark.parametrize("changes", [
    {"threshold_minutes": 0},
    {"enabled": True},
    {"notify_roles": "[]"},
    {"enable_site": None, "enable_wecom": None},
    {},
])
def test_partial_updates_do_not_enable_channels(context, changes):
    client, _, rule_id = context
    response = client.put(f"/api/v1/alert-rules/{rule_id}", json=changes)
    assert response.status_code == 200
    saved = response.json()
    assert saved["enable_site"] is False
    assert saved["enable_wecom"] is False
    for field, value in changes.items():
        if value is not None:
            assert saved[field] == value


def test_updating_unknown_rule_returns_chinese_error(context):
    client, _, _ = context
    response = client.put("/api/v1/alert-rules/999999", json={"enable_site": False})
    assert response.status_code == 404
    assert response.json() == {"detail": "规则不存在"}

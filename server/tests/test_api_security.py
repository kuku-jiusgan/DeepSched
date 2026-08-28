import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import protected_router
from app.api.access import (
    _operation_permission,
    require_management_user,
    require_notification_owner,
    require_project_editor_by_proj_id,
    require_slot_operator,
    require_task_editor,
)
from app.api.alert_rules import _push_config_response
from app.api.users import (
    MAX_LOGIN_FAILURES,
    _seed_admin,
    auth_token,
    check_login_throttle,
    create_token,
    get_current_user,
    record_login_failure,
    require_authenticated_user,
)
from app.core.database import Base
from app.models import AuthSession, User


def _api_routes(router):
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _api_routes(original_router)


class ApiSecurityTest(unittest.TestCase):
    def test_later_added_operations_use_their_own_button_permissions(self):
        self.assertEqual(
            ("/tasks/workspace", "pause"),
            _operation_permission("POST", "/api/v1/schedules/timeslots/1/pause"),
        )
        self.assertEqual(
            ("/projects/plan-breakdown", "save_draft"),
            _operation_permission("POST", "/api/v1/projects/1/plan-drafts/commit"),
        )
        self.assertEqual(
            ("/projects/resource-ledger", "manage_capabilities"),
            _operation_permission("PUT", "/api/v1/instruments/1/capabilities"),
        )
        self.assertEqual(
            ("/projects/resource-ledger", "manage_maintenance"),
            _operation_permission("POST", "/api/v1/instruments/1/maintenance"),
        )
        self.assertEqual(
            ("/schedule/rules", "toggle"),
            _operation_permission("PUT", "/api/v1/schedule-rules/1/toggle"),
        )
        self.assertEqual(
            ("/schedule/engine", "daily_roll"),
            _operation_permission("POST", "/api/v1/schedules/daily-roll"),
        )
        self.assertIsNone(_operation_permission("PUT", "/api/v1/notifications/1/read"))

    def test_every_business_route_has_authentication_dependency(self):
        routes = list(_api_routes(protected_router))
        self.assertGreater(len(routes), 60)
        dependency_calls = {
            dependency.dependency
            for dependency in protected_router.dependencies
        }
        self.assertIn(require_authenticated_user, dependency_calls)

    def test_sensitive_mutations_have_object_or_role_authorization(self):
        expected_dependencies = {
            ("POST", "/api/v1/instruments"): require_management_user,
            ("DELETE", "/api/v1/projects/{proj_id}"): require_project_editor_by_proj_id,
            ("PUT", "/api/v1/projects/tasks/{task_id}"): require_task_editor,
            ("POST", "/api/v1/schedules/timeslots/{slot_id}/start"): require_slot_operator,
            ("POST", "/api/v1/schedules/generate"): require_management_user,
            ("PUT", "/api/v1/notifications/read-all"): require_authenticated_user,
            ("PUT", "/api/v1/notifications/{nid}/read"): require_notification_owner,
        }
        routes = list(_api_routes(protected_router))
        route_by_operation = {
            (method, route.path): route
            for route in routes
            for method in route.methods
        }

        for operation, required_dependency in expected_dependencies.items():
            with self.subTest(operation=operation):
                route = route_by_operation[operation]
                dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
                self.assertIn(required_dependency, dependency_calls)

    def test_calendar_api_is_read_only(self):
        calendar_methods = {
            method
            for route in _api_routes(protected_router)
            if route.path.startswith("/api/v1/calendar")
            for method in route.methods
        }

        self.assertEqual({"GET"}, calendar_methods)

    def test_missing_authorization_header_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            auth_token(None)
        self.assertEqual(401, context.exception.status_code)

    def test_push_config_response_never_contains_secret(self):
        config = SimpleNamespace(
            id=1,
            wecom_enabled=True,
            wecom_corp_id="corp",
            wecom_agent_id="agent",
            wecom_secret="do-not-return",
        )

        payload = _push_config_response(config).model_dump()

        self.assertNotIn("wecom_secret", payload)
        self.assertTrue(payload["has_wecom_secret"])

    def test_admin_is_not_seeded_without_explicit_password(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            with patch("app.api.users.get_settings", return_value=SimpleNamespace(
                INITIAL_ADMIN_PASSWORD=None,
            )):
                _seed_admin(db)
            self.assertIsNone(db.query(User).filter(User.username == "admin").first())
        finally:
            db.close()

    def test_repeated_login_failures_are_throttled_per_account_and_ip(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/users/login",
                "headers": [],
                "client": ("203.0.113.10", 12345),
            }
        )
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            for _ in range(MAX_LOGIN_FAILURES):
                record_login_failure(request, "target-user", db)
            with self.assertRaises(HTTPException) as context:
                check_login_throttle(request, "target-user", db)
            self.assertEqual(429, context.exception.status_code)
            check_login_throttle(request, "different-user", db)
        finally:
            db.close()

    def test_auth_session_is_shared_through_database_and_stores_only_token_hash(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        creator_db = session_factory()
        user = User(username="analyst", display_name="分析员", role="分析员", is_active=True)
        creator_db.add(user)
        creator_db.commit()
        user_id = user.id
        token = create_token(user_id, user.username, creator_db)
        creator_db.commit()
        creator_db.close()

        resolver_db = session_factory()
        try:
            resolved = get_current_user(token, resolver_db)
            stored = resolver_db.query(AuthSession).one()
            self.assertEqual(user_id, resolved.id)
            self.assertNotEqual(token, stored.token_hash)
            self.assertEqual(64, len(stored.token_hash))
        finally:
            resolver_db.close()


if __name__ == "__main__":
    unittest.main()

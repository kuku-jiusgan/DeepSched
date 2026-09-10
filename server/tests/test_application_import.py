import subprocess
import sys
from pathlib import Path


def test_application_import_does_not_connect_to_database():
    result = subprocess.run(
        [sys.executable, "-c", """
from unittest.mock import patch
from fastapi.routing import APIRoute

with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError(
    "Application import must not connect to or migrate the database"
)):
    from app.main import app, health

assert health()["status"] == "ok"
assert any(
    isinstance(route, APIRoute) and route.path == "/api/v1/health"
    for route in app.routes
)
assert app.router.on_startup
assert app.router.on_shutdown
"""],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""End-to-end public dashboard password + authenticator login contract."""
from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.dashboard_auth import clear_providers, register_provider
from hermes_cli.dashboard_auth.cookies import SESSION_AT_COOKIE
from hermes_cli.dashboard_auth.routes import _reset_password_rate_limit
from plugins.dashboard_auth.basic import hash_password
from plugins.dashboard_auth.totp import TotpAuthProvider, _totp_at


@pytest.fixture
def client_and_provider(tmp_path):
    clear_providers()
    _reset_password_rate_limit()
    provider = TotpAuthProvider(
        username="owner", password_hash=hash_password("correct-horse-battery-staple"),
        secret=b"q" * 32, state_path=tmp_path / "totp.db",
    )
    register_provider(provider)
    previous = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = True
    client = TestClient(web_server.app, base_url="https://dashboard.example.test")
    yield client, provider
    clear_providers()
    _reset_password_rate_limit()
    web_server.app.state.auth_required = previous


def _pending_code(provider):
    conn = sqlite3.connect(provider._state_path)
    encrypted = conn.execute("SELECT pending_secret FROM account WHERE id=1").fetchone()[0]
    conn.close()
    return _totp_at(provider._decrypt(encrypted), int(time.time()) // 30)


def test_password_requires_enrollment_then_totp_before_session(client_and_provider):
    client, provider = client_and_provider
    first = client.post(
        "/auth/password-login",
        json={"provider": "totp", "username": "owner", "password": "correct-horse-battery-staple"},
    )
    assert first.status_code == 200
    assert first.json()["next"] == "/auth/totp"
    assert SESSION_AT_COOKIE not in first.headers.get("set-cookie", "")
    page = client.get("/auth/totp")
    assert page.status_code == 200
    assert "otpauth://totp/" in page.text
    complete = client.post("/auth/totp/verify", json={"code": _pending_code(provider)})
    assert complete.status_code == 200
    assert len(complete.json()["recovery_codes"]) == 8
    assert SESSION_AT_COOKIE in complete.headers.get("set-cookie", "")
    assert client.get("/api/auth/me").json()["provider"] == "totp"


def test_totp_write_rejects_cross_origin_request(client_and_provider):
    client, _provider = client_and_provider
    response = client.post(
        "/auth/password-login",
        headers={"Origin": "https://attacker.example"},
        json={"provider": "totp", "username": "owner", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 403

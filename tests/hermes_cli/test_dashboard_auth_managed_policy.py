"""An unreadable managed allowlist must not revive a legacy login provider."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.dashboard_auth import clear_providers, list_session_providers, register_provider
from hermes_cli.dashboard_auth.cookies import SESSION_AT_COOKIE
from hermes_cli.managed_scope import invalidate_managed_cache
from plugins.dashboard_auth.basic import BasicAuthProvider, hash_password


@pytest.fixture
def managed_auth(tmp_path, monkeypatch):
    home, managed = tmp_path / "home", tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    (home / "config.yaml").write_text("dashboard: {}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    for attr in ("auth_required", "trusted_public_hosts"):
        monkeypatch.setattr(web_server.app.state, attr, getattr(web_server.app.state, attr, None), raising=False)
    clear_providers()
    invalidate_managed_cache()
    provider = BasicAuthProvider(username="test-owner", password_hash=hash_password("test-only-password"), secret=b"x" * 32)
    register_provider(provider)
    yield home, managed, provider
    clear_providers()
    invalidate_managed_cache()


@pytest.mark.parametrize("failure", ["yaml", "empty", "null", "root_type", "dashboard_type", "unreadable", "missing_file", "missing_directory"])
def test_managed_policy_failure_keeps_public_dashboard_locked(managed_auth, failure):
    _home, managed, provider = managed_auth
    config = managed / "config.yaml"
    config.write_text("dashboard:\n  auth_providers: [other-provider]\n")
    assert list_session_providers() == []
    # Keep a real legacy session that would succeed if provider filtering vanished.
    legacy_session = provider.complete_password_login(username="test-owner", password="test-only-password")
    if failure == "yaml":
        config.write_text("dashboard: [\n")
    elif failure in {"empty", "null"}:
        config.write_text("" if failure == "empty" else "null\n")
    elif failure == "root_type":
        config.write_text("- not-a-mapping\n")
    elif failure == "dashboard_type":
        config.write_text("dashboard: invalid\n")
    else:
        config.unlink()
        if failure == "unreadable":
            config.mkdir()  # A real read failure on all host platforms, including root CI.
        elif failure == "missing_directory":
            managed.rmdir()
    assert list_session_providers() == []
    # The real public-bind configuration must keep the gate active even though no
    # permitted session provider remains; it must never switch to ungated mode.
    web_server._configure_auth_gate("0.0.0.0", False, None, None)
    assert web_server.app.state.auth_required is True
    client = TestClient(web_server.app, base_url="https://dashboard.example.test")
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set(SESSION_AT_COOKIE, legacy_session.access_token)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy_session.access_token}"}).status_code == 401
    assert client.post("/auth/password-login", json={
        "provider": "basic", "username": "test-owner", "password": "test-only-password",
    }).status_code == 404


def test_absent_managed_policy_preserves_profile_resolution_a_b_a(managed_auth, tmp_path, monkeypatch):
    home_a, _managed, provider = managed_auth
    home_b = tmp_path / "profile-b"
    home_b.mkdir()
    (home_b / "config.yaml").write_text("dashboard:\n  auth_providers: []\n")
    for home, expected in ((home_a, [provider]), (home_b, []), (home_a, [provider])):
        monkeypatch.setenv("HERMES_HOME", str(home))
        assert list_session_providers() == expected

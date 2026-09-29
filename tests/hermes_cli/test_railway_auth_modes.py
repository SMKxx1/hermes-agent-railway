"""Railway mode changes through real config, plugin discovery, JWTs and dashboard routes."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import hermes_yaml as yaml
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from scripts.railway_bootstrap import bootstrap


@pytest.fixture
def oidc_server():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-key", use="sig", alg="RS256")
    grants = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/.well-known/openid-configuration":
                self.respond({"issuer": issuer, "authorization_endpoint": issuer + "/authorize",
                              "token_endpoint": issuer + "/token", "jwks_uri": issuer + "/jwks"})
            elif self.path == "/jwks":
                self.respond({"keys": [jwk]})
            else:
                self.send_error(404)

        def do_POST(self):
            assert self.path == "/token"
            data = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            grants.append(data)
            subject = data.get("code", ["owner-subject"])[0]
            now = int(time.time())
            token = jwt.encode({"iss": issuer, "aud": "test-dashboard-client", "sub": subject,
                                "iat": now, "exp": now + 900}, key, algorithm="RS256",
                               headers={"kid": "test-key"})
            self.respond({"id_token": token, "access_token": "opaque-test-token",
                          "refresh_token": "test-refresh", "token_type": "Bearer"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    issuer = f"http://127.0.0.1:{server.server_port}"
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield issuer, grants
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_totp_oidc_totp_preserves_factor_and_enforces_managed_policy(tmp_path, monkeypatch, oidc_server):
    from hermes_cli import plugins, web_server
    from hermes_cli.dashboard_auth import clear_providers, get_provider, list_session_providers
    from hermes_cli.dashboard_auth.cookies import SESSION_AT_COOKIE
    from hermes_cli.dashboard_auth.routes import _reset_password_rate_limit
    from hermes_cli.env_loader import load_hermes_dotenv
    from hermes_cli.managed_scope import is_env_managed, is_key_managed
    from plugins.dashboard_auth.basic import hash_password
    from plugins.dashboard_auth.totp import _totp_at

    home, managed, seed = tmp_path / "home", tmp_path / "managed", tmp_path / "seed.yaml"
    issuer, grants = oidc_server
    seed.write_text(yaml.safe_dump({
        "terminal": {"backend": "local"},
        "dashboard": {
            "auth_providers": ["basic"],
            "totp_auth": {"username": "stale-owner", "password_hash": hash_password("stale-test-password")},
            "oauth": {"self_hosted": {"issuer": "https://stale-idp.example.test", "client_id": "stale-client",
                                      "client_secret": "stale-secret", "allowed_subjects": ["unapproved-subject"]}},
        },
    }))
    totp_env = {"HERMES_DASHBOARD_TOTP_AUTH_USERNAME": "owner",
                "HERMES_DASHBOARD_TOTP_AUTH_PASSWORD": "correct-horse-battery-staple"}
    oidc_env = {**totp_env, "HERMES_DASHBOARD_OIDC_ISSUER": issuer,
                "HERMES_DASHBOARD_OIDC_CLIENT_ID": "test-dashboard-client",
                "HERMES_DASHBOARD_PUBLIC_URL": "https://public.example.test",
                "HERMES_DASHBOARD_OIDC_ALLOWED_SUBJECTS": '["owner-subject"]'}
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    # Discover the real bundled auth manifests without activating unrelated backends.
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: Path(__file__).resolve().parents[2] / "plugins/dashboard_auth")
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)
    _reset_password_rate_limit()
    managers = []

    def restart(env):
        bootstrap(home, managed, seed, env)
        clear_providers()  # A container restart starts with an empty process registry.
        load_hermes_dotenv()
        manager = plugins.PluginManager()
        managers.append(manager)
        manager.discover_and_load()
        return TestClient(web_server.app, base_url="https://dashboard.example.test")

    try:
        client = restart(totp_env)
        assert [p.name for p in list_session_providers()] == ["totp"]
        provider = get_provider("totp")
        login = client.post("/auth/password-login", json={"provider": "totp", "username": "owner",
                                                        "password": totp_env["HERMES_DASHBOARD_TOTP_AUTH_PASSWORD"]})
        assert login.status_code == 200
        with sqlite3.connect(home / "dashboard-totp-auth.sqlite3") as conn:
            factor = provider._decrypt(conn.execute("SELECT pending_secret FROM account WHERE id=1").fetchone()[0])
        code = _totp_at(factor, int(time.time()) // 30)
        assert client.post("/auth/totp/verify", json={"code": code}).status_code == 200
        old_session = client.cookies.get(SESSION_AT_COOKIE)
        assert client.get("/api/auth/me").json()["provider"] == "totp"
        private_env = (home / ".env").read_bytes()
        factor_db = (home / "dashboard-totp-auth.sqlite3").read_bytes()

        client = restart(oidc_env)
        assert [p.name for p in list_session_providers()] == ["self-hosted"]
        assert get_provider("totp") is None
        assert (home / ".env").read_bytes() == private_env
        assert (home / "dashboard-totp-auth.sqlite3").read_bytes() == factor_db
        assert is_key_managed("dashboard.oauth.self_hosted.allowed_subjects")
        for key in ("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD", "HERMES_DASHBOARD_OIDC_CLIENT_SECRET",
                    "HERMES_DASHBOARD_OIDC_ALLOWED_SUBJECTS"):
            assert is_env_managed(key)
        assert get_provider("self-hosted")._client_secret == ""
        client.cookies.set(SESSION_AT_COOKIE, old_session)
        assert client.get("/api/auth/me").status_code == 401
        client.cookies.clear()
        assert client.post("/auth/password-login", json={"provider": "totp", "username": "owner",
                                                        "password": totp_env["HERMES_DASHBOARD_TOTP_AUTH_PASSWORD"]}).status_code == 404

        for subject, accepted in (("unapproved-subject", False), ("owner-subject", True)):
            client.cookies.clear()
            login = client.get("/auth/login?provider=self-hosted", follow_redirects=False)
            assert login.status_code in (302, 307)
            query = parse_qs(urlsplit(login.headers["location"]).query)
            assert query["code_challenge_method"] == ["S256"]
            assert query["redirect_uri"] == ["https://public.example.test/auth/callback"]
            callback = client.get("/auth/callback", params={"state": query["state"][0], "code": subject}, follow_redirects=False)
            assert callback.status_code == (302 if accepted else 400)
            assert (SESSION_AT_COOKIE in callback.headers.get("set-cookie", "")) is accepted
        assert grants[-1]["client_id"] == ["test-dashboard-client"]
        assert grants[-1]["redirect_uri"] == ["https://public.example.test/auth/callback"]
        assert "code_verifier" in grants[-1]
        assert client.get("/api/auth/me").json()["user_id"] == "owner-subject"
        oidc_session = client.cookies.get(SESSION_AT_COOKIE)

        client = restart(totp_env)
        assert [p.name for p in list_session_providers()] == ["totp"]
        assert get_provider("self-hosted") is None
        assert (home / ".env").read_bytes() == private_env
        values = dotenv_values(managed / ".env")
        assert values["HERMES_DASHBOARD_OIDC_ISSUER"] == ""
        assert values["HERMES_DASHBOARD_OIDC_ALLOWED_SUBJECTS"] == ""
        client.cookies.set(SESSION_AT_COOKIE, oidc_session)
        assert client.get("/api/auth/me").status_code == 401
        client.cookies.clear()
        login = client.post("/auth/password-login", json={"provider": "totp", "username": "owner",
                                                        "password": totp_env["HERMES_DASHBOARD_TOTP_AUTH_PASSWORD"]})
        assert login.status_code == 200
        assert "otpauth://" not in client.get("/auth/totp").text
        # The accepted next time step avoids replay without sleeping for a new code.
        code = _totp_at(factor, int(time.time()) // 30 + 1)
        assert client.post("/auth/totp/verify", json={"code": code}).status_code == 200
        assert client.get("/api/auth/me").json()["provider"] == "totp"
    finally:
        for manager in managers:
            manager.unload()
        clear_providers()
        _reset_password_rate_limit()

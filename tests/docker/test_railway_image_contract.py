"""End-to-end contracts for the public Railway image (local Docker only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.cookiejar
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest


def docker(*args, timeout=90):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, result.stderr[-3000:]
    return (result.stdout + (result.stderr if args and args[0] == "logs" else "")).strip()


@pytest.fixture(scope="module")
def railway_image():
    image = os.environ.get("HERMES_TEST_IMAGE")
    if not image:
        image = "hermes-public-contract:test"
        docker("build", "--build-arg", "HERMES_CUSTOM_REVISION=contract-test", "-t", image, ".", timeout=1200)
    return image


class Browser:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=data, headers={"Origin": self.base, "Content-Type": "application/json"})
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, response.read().decode(), response.headers


def wait_ready(browser, name):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            status, body, _ = browser.request("/api/status")
            if status == 200 and json.loads(body).get("gateway_running"):
                return
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    pytest.fail("Container did not become ready: " + docker("logs", "--tail", "35", name)[-2500:])


@pytest.fixture
def instance(railway_image):
    name = "hermes-public-test-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    try:
        docker("run", "-d", "--name", name, "-p", "127.0.0.1::9119", "-v", volume + ":/opt/data",
               "-e", "HERMES_DASHBOARD_TOTP_AUTH_USERNAME=owner",
               "-e", "HERMES_DASHBOARD_TOTP_AUTH_PASSWORD=local-contract-test-password",
               "-e", "OPENROUTER_API_KEY=not-a-real-provider-key", railway_image)
        inspect = json.loads(docker("inspect", name))[0]
        port = inspect["NetworkSettings"]["Ports"]["9119/tcp"][0]["HostPort"]
        browser = Browser("http://127.0.0.1:" + port)
        wait_ready(browser, name)
        yield name, browser
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


def test_image_runtime_and_content_contract(railway_image):
    config = json.loads(docker("image", "inspect", railway_image))[0]["Config"]
    assert config["Entrypoint"] == ["/opt/hermes/docker/entrypoint-dispatch.sh"]
    assert config["Cmd"] == ["gateway", "run"] and config["User"] == "root"
    assert config["Labels"]["org.opencontainers.image.revision"]
    assert config["Labels"]["org.opencontainers.image.base.digest"] == "sha256:1e32ed53357b867e2efdc9570040bd58b574d129bb783aadb513608255b99cb7"
    env = dict(x.split("=", 1) for x in config["Env"])
    assert env["HERMES_DASHBOARD"] == "1" and env["PORT"] == "9119"
    assert env["S6_BEHAVIOUR_IF_STAGE2_FAILS"] == "2"
    docker("run", "--rm", "--entrypoint", "/opt/hermes/.venv/bin/python", railway_image, "-c", """
from pathlib import Path
import importlib.metadata
import re
import tomllib
import gateway.run, tools.transcription_tools
from plugins.dashboard_auth.totp import TotpAuthProvider
root=Path('/opt/hermes')
normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
locked = {}
for package in tomllib.loads((root/'uv.lock').read_text())['package']:
    locked.setdefault(normalize(package['name']), set()).add(package['version'])
for package in importlib.metadata.distributions():
    name = normalize(package.metadata['Name'])
    assert package.version in locked.get(name, set()), (name, package.version)
assert (root/'hermes_cli/web_dist/index.html').is_file()
assert (root/'ui-tui/dist/entry.js').is_file()
for name in ['.git','.env','auth.json','HERMES_RAILWAY_IMPLEMENTATION_PLAN.md','HERMES_RAILWAY_IMPLEMENTATION_HANDOVER.md','contributors','mcp-research-data','scripts/release.py','agent/orchestrator.py','hermes_cli/route_research']:
    assert not (root/name).exists(), name
""")


def test_missing_credentials_stops_before_dashboard(railway_image):
    name = "hermes-public-invalid-" + uuid.uuid4().hex[:12]
    try:
        docker("run", "-d", "--name", name, railway_image)
        code = docker("wait", name, timeout=45)
        assert code != "0"
        logs = docker("logs", name)
        assert "Set HERMES_DASHBOARD_TOTP_AUTH_USERNAME" in logs
        assert "HERMES_DASHBOARD_READY" not in logs
    finally:
        subprocess.run(["docker", "rm", "-fv", name], capture_output=True)


def test_fresh_enrollment_persistence_and_legacy_login_denied(instance):
    name, browser = instance
    status, body, _ = browser.request("/api/auth/providers")
    assert status == 200
    assert [p["name"] for p in json.loads(body)["providers"]] == ["totp"]
    assert browser.request("/api/config")[0] == 401
    for provider in ("basic", "nous", "self-hosted"):
        assert browser.request("/auth/password-login", {"provider":provider,"username":"owner","password":"local-contract-test-password"})[0] == 404
    status, _, headers = browser.request("/auth/password-login", {"provider":"totp","username":"owner","password":"local-contract-test-password"})
    assert status == 200
    assert "hermes_totp_challenge" in str(headers) and "httponly" in str(headers).lower()
    assert browser.request("/api/config")[0] == 401
    status, page, _ = browser.request("/auth/totp")
    assert status == 200
    uri = html.unescape(re.search(r'href="(otpauth://[^"]+)"', page).group(1))
    secret = urllib.parse.parse_qs(urllib.parse.urlsplit(uri).query)["secret"][0]
    raw = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int(time.time()) // 30
    digest = hmac.new(raw, counter.to_bytes(8,"big"), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = str((int.from_bytes(digest[offset:offset+4], "big") & 0x7fffffff) % 1000000).zfill(6)
    status, body, _ = browser.request("/auth/totp/verify", {"code":code})
    assert status == 200
    assert len(json.loads(body)["recovery_codes"]) == 8
    assert browser.request("/api/config")[0] == 200
    # A signed browser session must not authorize another origin's form POST.
    request = urllib.request.Request(
        browser.base + "/api/ops/config-migrate", data=b"",
        headers={"Origin": "https://untrusted.example", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with pytest.raises(urllib.error.HTTPError) as rejected:
        browser.opener.open(request, timeout=10)
    assert rejected.value.code == 403
    # A real restart must preserve both factor enrollment and the current session.
    docker("restart", name)
    port = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]["9119/tcp"][0]["HostPort"]
    browser.base = "http://127.0.0.1:" + port
    wait_ready(browser, name)
    assert browser.request("/api/config")[0] == 200
    docker("exec", "-u", "hermes", name, "/opt/hermes/.venv/bin/python", "-c", """
import os
from pathlib import Path
from dotenv import dotenv_values
h=Path('/opt/data'); values=dotenv_values(h/'.env')
assert len(values['API_SERVER_KEY']) >= 32
assert len(values['HERMES_DASHBOARD_TOTP_AUTH_SECRET']) >= 32
assert 'HERMES_DASHBOARD_TOTP_AUTH_PASSWORD' not in values
assert (h/'.env').stat().st_mode & 0o777 == 0o600
assert (h/'dashboard-totp-auth.sqlite3').stat().st_mode & 0o777 == 0o600
assert os.getuid() != 0
""")
    docker("exec", "-u", "hermes", name, "/opt/hermes/.venv/bin/python", "-m", "plugins.dashboard_auth.totp", "reset", "--confirm")
    assert browser.request("/api/config")[0] == 401


def test_owner_created_credential_files_remain_readable(instance):
    name, browser = instance
    docker("exec", "-u", "root", name, "/opt/hermes/.venv/bin/python", "-c", """
from pathlib import Path
for name in ('.op.env', '.anthropic_oauth.json'):
    p=Path('/opt/data')/name
    p.write_text('')
    p.chmod(0o600)
""")
    docker("restart", name)
    port = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]["9119/tcp"][0]["HostPort"]
    browser.base = "http://127.0.0.1:" + port
    wait_ready(browser, name)
    docker("exec", "-u", "hermes", name, "/opt/hermes/.venv/bin/python", "-c", """
import os
from pathlib import Path
for name in ('.op.env', '.anthropic_oauth.json'):
    p=Path('/opt/data')/name
    assert p.read_text() == ''
    assert p.stat().st_uid == os.getuid()
    assert p.stat().st_mode & 0o777 == 0o600
""")


def test_config_migration_failure_stops_services(railway_image, tmp_path):
    name = 'hermes-public-migration-' + uuid.uuid4().hex[:12]
    failure = tmp_path / 'migration-failure.py'
    failure.write_text('raise SystemExit(1)\n')
    try:
        docker('run', '-d', '--name', name,
               '-v', str(failure) + ':/opt/hermes/scripts/docker_config_migrate.py:ro',
               '-e', 'HERMES_DASHBOARD_TOTP_AUTH_USERNAME=owner',
               '-e', 'HERMES_DASHBOARD_TOTP_AUTH_PASSWORD=local-contract-test-password', railway_image)
        assert docker('wait', name, timeout=45) != '0'
        logs = docker('logs', name)
        assert 'config migration failed; refusing to start services' in logs
        assert 'HERMES_DASHBOARD_READY' not in logs
    finally:
        subprocess.run(['docker','rm','-fv',name],capture_output=True)

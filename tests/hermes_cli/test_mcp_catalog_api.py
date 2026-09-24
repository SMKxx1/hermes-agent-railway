"""Catalog HTTP setup uses the real installer without terminal prompts."""
from __future__ import annotations

import pytest
import yaml
from starlette.testclient import TestClient


@pytest.fixture
def catalog_api(tmp_path, monkeypatch):
    from hermes_cli import web_server
    from hermes_cli import mcp_catalog

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(tmp_path / "managed"))
    monkeypatch.setenv("DEMO_MCP_KEY", "")
    catalog = tmp_path / "catalog"
    entry_dir = catalog / "demo"
    entry_dir.mkdir(parents=True)
    manifest = entry_dir / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({
        "manifest_version": 1, "name": "demo", "description": "A test server.",
        "transport": {"type": "stdio", "command": "python", "args": ["server.py"]},
        "auth": {"type": "api_key", "env": [
            {"name": "DEMO_MCP_KEY", "secret": True},
            {"name": "DEMO_BASE_URL", "secret": False, "default": "https://example.test"},
        ]},
    }), encoding="utf-8")
    monkeypatch.setenv("HERMES_OPTIONAL_MCPS", str(catalog))
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(mcp_catalog, "_probe_tools", lambda *_: pytest.fail("HTTP install probed a server"))
    monkeypatch.setattr(mcp_catalog, "_prompt_input", lambda *a, **kw: pytest.fail("HTTP install prompted on stdin"))
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return client, home, manifest


def test_catalog_api_accepts_existing_railway_key_without_form_value(catalog_api, monkeypatch):
    client, home, _ = catalog_api
    monkeypatch.setenv("DEMO_MCP_KEY", "railway-test-key")
    response = client.post("/api/mcp/catalog/install", json={"name": "demo"})
    assert response.status_code == 200, response.text
    raw = (home / "config.yaml").read_text(encoding="utf-8")
    server = yaml.safe_load(raw)["mcp_servers"]["demo"]
    assert server["env"]["DEMO_MCP_KEY"] == "${DEMO_MCP_KEY}"
    assert server["env"]["DEMO_BASE_URL"] == "https://example.test"
    assert "railway-test-key" not in raw + response.text


def test_catalog_api_validates_required_and_unknown_inputs(catalog_api):
    client, home, _ = catalog_api
    missing = client.post("/api/mcp/catalog/install", json={"name": "demo"})
    assert missing.status_code == 400
    assert "DEMO_MCP_KEY" in missing.json()["detail"]
    unknown = client.post("/api/mcp/catalog/install", json={"name": "demo", "env": {"UNDECLARED_KEY": "secret"}})
    assert unknown.status_code == 400
    assert "secret" not in unknown.text
    assert not (home / "config.yaml").exists()


def test_catalog_api_profile_credentials_and_config_are_colocated(catalog_api, tmp_path, monkeypatch):
    from hermes_cli import web_server
    from dotenv import dotenv_values

    client, home, _ = catalog_api
    profile = tmp_path / "profiles" / "work"
    profile.mkdir(parents=True)
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: profile)
    response = client.post("/api/mcp/catalog/install?profile=work", json={
        "name": "demo", "enable": False,
        "env": {"DEMO_MCP_KEY": "profile-test-key", "DEMO_BASE_URL": "https://work.test"},
    })
    assert response.status_code == 200, response.text
    server = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))["mcp_servers"]["demo"]
    assert server["enabled"] is False
    assert server["env"]["DEMO_BASE_URL"] == "https://work.test"
    assert dotenv_values(profile / ".env")["DEMO_MCP_KEY"] == "profile-test-key"
    assert "DEMO_BASE_URL" not in dotenv_values(profile / ".env")
    assert not (home / "config.yaml").exists()


def test_git_catalog_api_passes_settings_but_not_secrets_to_child(catalog_api, monkeypatch):
    from hermes_cli import web_server

    client, _, manifest = catalog_api
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["install"] = {"type": "git", "url": "https://example.test/repo.git", "ref": "a" * 40}
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    calls = []
    monkeypatch.setattr(web_server, "_spawn_hermes_action", lambda argv, action: calls.append(argv))
    response = client.post("/api/mcp/catalog/install", json={
        "name": "official/demo", "enable": False,
        "env": {"DEMO_MCP_KEY": "private-test-key", "DEMO_BASE_URL": "https://work.test"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["background"] is True
    assert len(calls) == 1
    assert "private-test-key" not in " ".join(calls[0])
    assert "--yes" in calls[0] and "--no-probe" in calls[0] and "--disabled" in calls[0]
    assert "DEMO_BASE_URL=https://work.test" in calls[0]

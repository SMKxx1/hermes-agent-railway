"""Real CLI -> config -> stdio MCP contracts for agent-driven setup."""
from __future__ import annotations

import subprocess
import sys

import pytest
import yaml


@pytest.fixture
def mcp_home(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(tmp_path / "managed"))
    monkeypatch.setenv("PYTHONUTF8", "1")
    return home


@pytest.fixture
def local_server(tmp_path):
    server = tmp_path / "custom_server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('local-test')\n"
        "@server.tool()\n"
        "def echo(message: str) -> str:\n"
        "    return message\n"
        "server.run()\n",
        encoding="utf-8",
    )
    return server


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "mcp", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", timeout=60, env=env,
    )


def test_add_with_closed_stdin_persists_working_custom_server(mcp_home, local_server):
    result = run_cli("add", "custom", "--command", sys.executable, "--args", str(local_server))
    assert result.returncode == 0, result.stdout + result.stderr
    config = yaml.safe_load((mcp_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["mcp_servers"]["custom"]["enabled"] is True
    tested = run_cli("test", "custom")
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert "echo" in tested.stdout

    # Exercise the actual saved transport and execute user-written server code.
    from hermes_cli.mcp_config import _get_mcp_servers, _resolve_mcp_server_config
    from tools.mcp_tool import _connect_server, _ensure_mcp_loop, _run_on_mcp_loop, _stop_mcp_loop_if_idle

    async def call_echo():
        server = await _connect_server("custom", _resolve_mcp_server_config(_get_mcp_servers()["custom"]))
        try:
            result = await server.session.call_tool("echo", {"message": "custom code executed"})
            assert not result.isError
            assert result.content[0].text == "custom code executed"
        finally:
            await server.shutdown()

    _ensure_mcp_loop()
    try:
        _run_on_mcp_loop(call_echo(), timeout=30)
    finally:
        _stop_mcp_loop_if_idle()


def test_failed_custom_server_is_a_nonzero_exit(mcp_home):
    result = run_cli("add", "broken", "--command", sys.executable, "--args", "-c", "raise SystemExit(1)")
    assert result.returncode != 0, result.stdout + result.stderr
    config_file = mcp_home / "config.yaml"
    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    assert "broken" not in config.get("mcp_servers", {})
    assert run_cli("test", "broken").returncode != 0


def test_catalog_declared_credentials_reach_stdio_process(mcp_home, tmp_path, monkeypatch):
    """A saved credential must reach its server, but unrelated keys must not."""
    server = tmp_path / "authenticated_server.py"
    receipt = tmp_path / "connected"
    server.write_text(
        "import os\nfrom pathlib import Path\n"
        "assert os.environ['DEMO_MCP_TOKEN'] == 'test-credential'\n"
        "assert os.environ['DEMO_BASE_URL'] == 'https://example.test'\n"
        "assert 'UNRELATED_API_KEY' not in os.environ\n"
        f"Path({str(receipt)!r}).write_text('connected')\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('authenticated-test')\n"
        "@server.tool()\n"
        "def ready() -> bool:\n    return True\n"
        "server.run()\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog"
    entry = catalog / "authenticated"
    entry.mkdir(parents=True)
    (entry / "manifest.yaml").write_text(yaml.safe_dump({
        "manifest_version": 1,
        "name": "authenticated",
        "description": "Local authenticated fixture.",
        "transport": {"type": "stdio", "command": sys.executable, "args": [str(server)]},
        "auth": {"type": "api_key", "env": [
            {"name": "DEMO_MCP_TOKEN", "secret": True},
            {"name": "DEMO_BASE_URL", "secret": False},
        ]},
    }), encoding="utf-8")
    monkeypatch.setenv("HERMES_OPTIONAL_MCPS", str(catalog))
    monkeypatch.setenv("DEMO_BASE_URL", "https://example.test")
    monkeypatch.setenv("UNRELATED_API_KEY", "must-not-reach-the-server")
    (mcp_home / ".env").write_text("DEMO_MCP_TOKEN=test-credential\n", encoding="utf-8")

    installed = run_cli("install", "authenticated")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert receipt.exists(), installed.stdout + installed.stderr
    raw = (mcp_home / "config.yaml").read_text(encoding="utf-8")
    assert "test-credential" not in raw
    saved = yaml.safe_load(raw)["mcp_servers"]["authenticated"]
    assert saved["env"]["DEMO_MCP_TOKEN"] == "${DEMO_MCP_TOKEN}"
    assert saved["env"]["DEMO_BASE_URL"] == "https://example.test"
    # Restart-equivalent: the non-secret setting must survive without the
    # one-shot environment that was present during install.
    monkeypatch.delenv("DEMO_BASE_URL")
    tested = run_cli("test", "authenticated")
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert "ready" in tested.stdout


def test_headless_oauth_saves_config_without_waiting_for_browser(mcp_home):
    result = run_cli("add", "oauth", "--yes", "--url", "http://127.0.0.1:1/mcp", "--auth", "oauth")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Authenticate" in result.stdout
    assert "connection not tested" in result.stdout
    cfg = yaml.safe_load((mcp_home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["oauth"]["auth"] == "oauth"


def test_headless_header_auth_reports_missing_key(mcp_home):
    result = run_cli("add", "private", "--yes", "--url", "http://127.0.0.1:1/mcp", "--auth", "header")
    assert result.returncode != 0
    assert "MCP_PRIVATE_API_KEY" in result.stdout
    assert "private" not in (yaml.safe_load((mcp_home / "config.yaml").read_text()) if (mcp_home / "config.yaml").exists() else {}).get("mcp_servers", {})


def test_explicit_no_probe_and_replace_are_scriptable(mcp_home, local_server):
    args = ("add", "custom", "--no-probe", "--command", sys.executable, "--args", str(local_server))
    assert run_cli(*args).returncode == 0
    original = (mcp_home / "config.yaml").read_bytes()
    assert run_cli(*args).returncode != 0
    assert (mcp_home / "config.yaml").read_bytes() == original
    result = run_cli("add", "custom", "--yes", "--no-probe", "--url", "http://127.0.0.1:1/mcp", "--auth", "none")
    assert result.returncode == 0, result.stdout + result.stderr
    cfg = yaml.safe_load((mcp_home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["custom"]["url"] == "http://127.0.0.1:1/mcp"
    assert "command" not in cfg["mcp_servers"]["custom"]

"""Railway execution defaults through real config loading and code execution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.railway_bootstrap import _seed_execution_defaults


def defaults():
    return yaml.safe_load((Path(__file__).resolve().parents[2] / "deploy/railway/defaults.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("mode", [None, "manual", "smart", "off"])
def test_execution_upgrade_preserves_owner_choices_and_comments(tmp_path, mode):
    config = tmp_path / "config.yaml"
    original = "# owner settings\nmodel:\n  default: custom-model\n"
    if mode is not None:
        original += f'approvals:\n  mode: "{mode}"\n'
    config.write_text(original, encoding="utf-8")
    _seed_execution_defaults(config, defaults(), uid=None, gid=None)
    updated = config.read_text(encoding="utf-8")
    parsed = yaml.safe_load(updated)
    assert parsed["model"]["default"] == "custom-model"
    assert parsed["approvals"]["mode"] == (mode or "off")
    assert "# owner settings" in updated
    if mode is not None:
        assert updated == original
    _seed_execution_defaults(config, defaults(), uid=None, gid=None)
    assert config.read_text(encoding="utf-8") == updated


@pytest.mark.parametrize("mode", [None, "manual"])
def test_gateway_can_execute_custom_code_with_railway_defaults(tmp_path, monkeypatch, mode):
    from tools.approval import set_current_session_key, reset_current_session_key
    from tools.code_execution_tool import execute_code

    home = tmp_path / "home"
    workspace = home / "workspace"
    workspace.mkdir(parents=True)
    config = defaults()
    if mode is not None:
        config["approvals"]["mode"] = mode
    config["terminal"]["cwd"] = str(workspace)
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(tmp_path / "managed"))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(workspace))
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    output = workspace / "custom-result.txt"
    token = set_current_session_key("api_server:railway-test")
    try:
        result = json.loads(execute_code(
            f"from pathlib import Path\nPath({str(output)!r}).write_text(str(sum(range(11))))\nprint('executed')",
            task_id="railway-execution-test",
        ))
    finally:
        reset_current_session_key(token)
    if mode == "manual":
        assert result["status"] == "error", result
        assert not output.exists()
    else:
        assert result["status"] == "success", result
        assert "executed" in result["output"]
        assert output.read_text() == "55"

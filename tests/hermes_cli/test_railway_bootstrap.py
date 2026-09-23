"""Fresh-instance, persistence and Railway credential ownership contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from scripts.railway_bootstrap import BootstrapError, bootstrap


@pytest.fixture
def installation(tmp_path):
    seed = tmp_path / "defaults.yaml"
    seed.write_text(yaml.safe_dump({"model": {"provider": "openrouter"}, "terminal": {"backend": "local"}}))
    home, managed = tmp_path / "home", tmp_path / "managed"
    env = {"HERMES_DASHBOARD_TOTP_AUTH_USERNAME": "owner", "HERMES_DASHBOARD_TOTP_AUTH_PASSWORD": "test-only-password-123"}
    return home, managed, seed, env


def test_fresh_instances_generate_private_distinct_credentials(installation, tmp_path):
    home, managed, seed, env = installation
    assert bootstrap(home, managed, seed, env)["config_seeded"]
    values = dotenv_values(home / ".env", interpolate=False)
    assert len(values["API_SERVER_KEY"]) >= 32
    assert len(values["HERMES_DASHBOARD_TOTP_AUTH_SECRET"]) >= 32
    assert "HERMES_DASHBOARD_TOTP_AUTH_PASSWORD" not in values
    assert (home / ".env").stat().st_mode & 0o777 == 0o600
    assert (managed / ".env").stat().st_mode & 0o777 == 0o640
    other = tmp_path / "other"
    bootstrap(other, tmp_path / "other-managed", seed, env)
    other_values = dotenv_values(other / ".env", interpolate=False)
    assert values["API_SERVER_KEY"] != other_values["API_SERVER_KEY"]
    assert values["HERMES_DASHBOARD_TOTP_AUTH_SECRET"] != other_values["HERMES_DASHBOARD_TOTP_AUTH_SECRET"]


def test_restart_preserves_owner_configuration_and_internal_credentials(installation):
    home, managed, seed, env = installation
    bootstrap(home, managed, seed, env)
    initial = (home / ".env").read_bytes()
    (home / "config.yaml").write_text("model:\n  provider: anthropic\n")
    result = bootstrap(home, managed, seed, env)
    assert not result["config_seeded"]
    assert (home / ".env").read_bytes() == initial
    assert yaml.safe_load((home / "config.yaml").read_text())["model"]["provider"] == "anthropic"


def test_railway_rotation_and_removal_override_stale_user_key(installation, monkeypatch):
    from hermes_cli.env_loader import _apply_managed_env
    from hermes_cli import managed_scope

    home, managed, seed, env = installation
    env["OPENROUTER_API_KEY"] = "test-first-key"
    bootstrap(home, managed, seed, env)
    with (home / ".env").open("a") as f:
        f.write("OPENROUTER_API_KEY=test-stale-key\n")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-stale-key")
    env["OPENROUTER_API_KEY"] = "test-rotated-key"
    result = bootstrap(home, managed, seed, env)
    _apply_managed_env()
    assert os.environ["OPENROUTER_API_KEY"] == "test-rotated-key"
    assert result["overridden_names"] == ["OPENROUTER_API_KEY"]
    env.pop("OPENROUTER_API_KEY")
    bootstrap(home, managed, seed, env)
    _apply_managed_env()
    assert os.environ["OPENROUTER_API_KEY"] == ""
    assert managed_scope.is_env_managed("OPENROUTER_API_KEY")


def test_additional_provider_removal_remains_authoritative(installation):
    home, managed, seed, env = installation
    env["EXAMPLE_PROVIDER_API_KEY"] = "test-only-credential"
    bootstrap(home, managed, seed, env)
    env.pop("EXAMPLE_PROVIDER_API_KEY")
    bootstrap(home, managed, seed, env)
    assert dotenv_values(managed / ".env")["EXAMPLE_PROVIDER_API_KEY"] == ""
    assert "test-only-credential" not in (home / ".railway-managed-keys.json").read_text()


def test_password_is_literal_with_dollar_quotes_and_backslash(installation, monkeypatch):
    from hermes_cli.env_loader import _apply_managed_env
    from hermes_cli import managed_scope

    home, managed, seed, env = installation
    password = "literal-${HOME}-'quoted'-\\password"
    env["HERMES_DASHBOARD_TOTP_AUTH_PASSWORD"] = password
    bootstrap(home, managed, seed, env)
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    _apply_managed_env()
    assert os.environ["HERMES_DASHBOARD_TOTP_AUTH_PASSWORD"] == password
    assert managed_scope.load_managed_env()["HERMES_DASHBOARD_TOTP_AUTH_PASSWORD"] == password


def test_managed_policy_requires_totp_and_loopback(installation):
    home, managed, seed, env = installation
    bootstrap(home, managed, seed, env)
    policy = yaml.safe_load((managed / "config.yaml").read_text())
    assert policy["dashboard"]["auth_providers"] == ["totp"]
    assert policy["platforms"]["api_server"]["extra"]["host"] == "127.0.0.1"


def test_managed_password_rotation_cannot_revive_stale_config_hash(installation, monkeypatch):
    from hermes_cli.dashboard_auth import InvalidCredentialsError
    from hermes_cli.env_loader import load_hermes_dotenv
    from plugins.dashboard_auth.basic import hash_password
    from plugins.dashboard_auth.totp import _build_provider

    home, managed, seed, env = installation
    old_password = "stale-config-password"
    seed.write_text(yaml.safe_dump({
        "terminal": {"backend": "local"},
        "dashboard": {"totp_auth": {"password_hash": hash_password(old_password)}},
    }))
    bootstrap(home, managed, seed, env)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    load_hermes_dotenv()
    provider = _build_provider()
    with pytest.raises(InvalidCredentialsError):
        provider.begin_password_login(username="owner", password=old_password)
    assert provider.begin_password_login(
        username="owner", password=env["HERMES_DASHBOARD_TOTP_AUTH_PASSWORD"],
    )


@pytest.mark.parametrize("target", [".env", "config.yaml", ".railway-managed-keys.json"])
def test_refuses_symlinked_instance_files(installation, tmp_path, target):
    home, managed, seed, env = installation
    home.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    (home / target).symlink_to(victim)
    with pytest.raises(BootstrapError, match="symlink"):
        bootstrap(home, managed, seed, env)
    assert victim.read_text() == "untouched"


def test_missing_password_fails_before_writing_volume(installation):
    home, managed, seed, env = installation
    env.pop("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD")
    with pytest.raises(BootstrapError, match="PASSWORD"):
        bootstrap(home, managed, seed, env)
    assert not home.exists()


def test_removing_internal_secret_override_uses_persistent_generated_value(installation):
    home, managed, seed, env = installation
    key = "HERMES_DASHBOARD_TOTP_AUTH_SECRET"
    env[key] = "t" * 48
    bootstrap(home, managed, seed, env)
    assert key not in dotenv_values(home / ".env")
    env.pop(key)
    bootstrap(home, managed, seed, env)
    assert len(dotenv_values(home / ".env")[key]) >= 32
    assert key not in dotenv_values(managed / ".env")


def test_multiline_input_rejected_without_exposing_value(installation):
    home, managed, seed, env = installation
    env["OPENROUTER_API_KEY"] = "private-test-value\ninjected=yes"
    with pytest.raises(BootstrapError) as failure:
        bootstrap(home, managed, seed, env)
    assert "private-test-value" not in str(failure.value)

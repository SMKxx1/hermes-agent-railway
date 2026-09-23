"""Security contracts for the bundled password + TOTP dashboard provider."""
from __future__ import annotations

import sqlite3
import time
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli.dashboard_auth import (
    DashboardAuthProvider, InvalidCredentialsError, RefreshExpiredError,
    clear_providers, list_session_providers, register_provider,
)
from hermes_cli.dashboard_auth import registry as auth_registry
from plugins.dashboard_auth.basic import hash_password
import plugins.dashboard_auth.totp as totp_plugin
from plugins.dashboard_auth.totp import TotpAuthProvider, _totp_at


@pytest.fixture
def provider(tmp_path):
    return TotpAuthProvider(
        username="owner",
        password_hash=hash_password("correct-horse-battery-staple"),
        secret=b"t" * 32,
        state_path=tmp_path / "private" / "totp.sqlite3",
        issuer="Test Hermes",
    )


def _pending_code(provider: TotpAuthProvider) -> str:
    conn = sqlite3.connect(provider._state_path)
    encrypted = conn.execute("SELECT pending_secret FROM account WHERE id=1").fetchone()[0]
    conn.close()
    return _totp_at(provider._decrypt(encrypted), int(time.time()) // 30)


def _enroll(provider: TotpAuthProvider):
    challenge = provider.begin_password_login(
        username="owner", password="correct-horse-battery-staple"
    )
    assert challenge.stage == "enroll"
    return provider.complete_totp_challenge(token=challenge.token, code=_pending_code(provider))


def test_password_only_never_mints_a_session(provider):
    with pytest.raises(InvalidCredentialsError):
        provider.complete_password_login(username="owner", password="correct-horse-battery-staple")
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    assert challenge.stage == "enroll"
    assert provider.verify_session(access_token="anything") is None


def test_enrollment_provisions_uri_and_one_time_hashed_recovery_codes(provider):
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    details = provider.challenge_details(challenge.token)
    assert details["otpauth_uri"].startswith("otpauth://totp/")
    completion = provider.complete_totp_challenge(token=challenge.token, code=_pending_code(provider))
    assert len(completion.recovery_codes) == 8
    conn = sqlite3.connect(provider._state_path)
    stored = [r[0] for r in conn.execute("SELECT digest FROM recovery_code")]
    conn.close()
    assert all(code.encode() not in stored for code in completion.recovery_codes)
    assert provider.verify_session(access_token=completion.session.access_token)


def test_totp_code_and_challenge_cannot_be_replayed(provider):
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    code = _pending_code(provider)
    provider.complete_totp_challenge(token=challenge.token, code=code)
    with pytest.raises(InvalidCredentialsError):
        provider.complete_totp_challenge(token=challenge.token, code=code)


def test_recovery_consumption_resets_factor_and_invalidates_sessions(provider):
    first = _enroll(provider)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    recovered = provider.recover_totp_challenge(
        token=challenge.token, recovery_code=first.recovery_codes[0]
    )
    assert recovered.stage == "enroll"
    assert provider.verify_session(access_token=first.session.access_token) is None
    with pytest.raises(RefreshExpiredError):
        provider.refresh_session(refresh_token=first.session.refresh_token)
    with pytest.raises(InvalidCredentialsError):
        provider.recover_totp_challenge(token=challenge.token, recovery_code=first.recovery_codes[0])


def test_password_alone_cannot_replace_recovery_enrollment(provider):
    first = _enroll(provider)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    recovered = provider.recover_totp_challenge(
        token=challenge.token, recovery_code=first.recovery_codes[0],
    )
    # Another worker must not mistake a recovery reset for first-time setup.
    restarted = TotpAuthProvider(
        username="owner", password_hash=provider._password_hash,
        secret=provider._secret, state_path=provider._state_path,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(
            lambda _n: restarted.begin_password_login(username="owner", password="correct-horse-battery-staple"),
            range(2),
        ))
    for attempt in attempts:
        assert attempt.stage == "verify"
        assert "otpauth_uri" not in restarted.challenge_details(attempt.token)
        with pytest.raises(InvalidCredentialsError):
            restarted.complete_totp_challenge(token=attempt.token, code=_pending_code(restarted))
    assert restarted.challenge_details(recovered.token)["stage"] == "enroll"
    completion = restarted.complete_totp_challenge(token=recovered.token, code=_pending_code(restarted))
    assert restarted.verify_session(access_token=completion.session.access_token) is not None
    assert restarted.begin_password_login(
        username="owner", password="correct-horse-battery-staple",
    ).stage == "verify"


def test_expired_recovery_can_restart_with_unused_recovery_code(provider, monkeypatch):
    first = _enroll(provider)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    recovered = provider.recover_totp_challenge(token=challenge.token, recovery_code=first.recovery_codes[0])
    later = time.time() + 301
    monkeypatch.setattr(totp_plugin.time, "time", lambda: later)
    with pytest.raises(InvalidCredentialsError):
        provider.challenge_details(recovered.token)
    password_only = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    assert password_only.stage == "verify"
    recovered = provider.recover_totp_challenge(token=password_only.token, recovery_code=first.recovery_codes[1])
    assert recovered.stage == "enroll"
    completion = provider.complete_totp_challenge(token=recovered.token, code=_pending_code(provider))
    assert provider.verify_session(access_token=completion.session.access_token) is not None
    # Successful enrollment also retires every unused code from the old set.
    new_login = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    with pytest.raises(InvalidCredentialsError):
        provider.recover_totp_challenge(token=new_login.token, recovery_code=first.recovery_codes[2])


def test_owner_reset_unlocks_abandoned_recovery(provider):
    first = _enroll(provider)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    provider.recover_totp_challenge(token=challenge.token, recovery_code=first.recovery_codes[0])
    provider.reset_factor_locally()
    assert provider.begin_password_login(
        username="owner", password="correct-horse-battery-staple",
    ).stage == "enroll"


def test_legacy_database_migration_preserves_and_protects_recovery_challenge(provider):
    first = _enroll(provider)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    recovered = provider.recover_totp_challenge(token=challenge.token, recovery_code=first.recovery_codes[0])
    with provider._connect() as conn:
        # The pre-fix implementation deleted ALL recovery hashes immediately
        # and had no flag distinguishing this state from first-time setup.
        conn.execute("DELETE FROM recovery_code")
        conn.execute("ALTER TABLE account DROP COLUMN recovery_pending")
    migrated = TotpAuthProvider(
        username="owner", password_hash=provider._password_hash,
        secret=provider._secret, state_path=provider._state_path,
    )
    assert migrated.begin_password_login(
        username="owner", password="correct-horse-battery-staple",
    ).stage == "verify"
    complete = migrated.complete_totp_challenge(token=recovered.token, code=_pending_code(migrated))
    assert migrated.verify_session(access_token=complete.session.access_token) is not None


def test_expired_legacy_recovery_without_codes_requires_owner_reset(provider, monkeypatch):
    first = _enroll(provider)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    recovered = provider.recover_totp_challenge(token=challenge.token, recovery_code=first.recovery_codes[0])
    with provider._connect() as conn:
        conn.execute("DELETE FROM recovery_code")
        conn.execute("ALTER TABLE account DROP COLUMN recovery_pending")
    later = time.time() + 301
    monkeypatch.setattr(totp_plugin.time, "time", lambda: later)
    migrated = TotpAuthProvider(
        username="owner", password_hash=provider._password_hash,
        secret=provider._secret, state_path=provider._state_path,
    )
    with pytest.raises(InvalidCredentialsError):
        migrated.challenge_details(recovered.token)
    login = migrated.begin_password_login(username="owner", password="correct-horse-battery-staple")
    assert login.stage == "verify"
    with pytest.raises(InvalidCredentialsError):
        migrated.recover_totp_challenge(token=login.token, recovery_code=first.recovery_codes[1])
    migrated.reset_factor_locally()
    completion = _enroll(migrated)
    assert migrated.verify_session(access_token=completion.session.access_token) is not None


@pytest.mark.parametrize("transition", ["password_rotation", "local_reset"])
@pytest.mark.parametrize("expired", [False, True])
def test_legacy_pending_setup_after_rotation_or_reset_migrates_conservatively(
    provider, monkeypatch, transition, expired,
):
    password = "correct-horse-battery-staple"
    if transition == "password_rotation":
        password = "rotated-password-before-first-enrollment"
        provider = TotpAuthProvider(
            username="owner", password_hash=hash_password(password),
            secret=provider._secret, state_path=provider._state_path,
        )
    else:
        provider.reset_factor_locally()
    pending = provider.begin_password_login(username="owner", password=password)
    assert pending.stage == "enroll"
    with provider._connect() as conn:
        conn.execute("ALTER TABLE account DROP COLUMN recovery_pending")
    if expired:
        later = time.time() + 301
        monkeypatch.setattr(totp_plugin.time, "time", lambda: later)
    migrated = TotpAuthProvider(
        username="owner", password_hash=provider._password_hash,
        secret=provider._secret, state_path=provider._state_path,
    )
    # Legacy setup with a bumped version is indistinguishable from recovery.
    # Migration must protect the existing challenge rather than reopen setup.
    assert migrated.begin_password_login(username="owner", password=password).stage == "verify"
    if expired:
        with pytest.raises(InvalidCredentialsError):
            migrated.challenge_details(pending.token)
        migrated.reset_factor_locally()
        pending = migrated.begin_password_login(username="owner", password=password)
    completion = migrated.complete_totp_challenge(token=pending.token, code=_pending_code(migrated))
    assert migrated.verify_session(access_token=completion.session.access_token) is not None


def test_legacy_database_migration_preserves_enrolled_factor_and_session(provider):
    first = _enroll(provider)
    with provider._connect() as conn:
        conn.execute("ALTER TABLE account DROP COLUMN recovery_pending")
    migrated = TotpAuthProvider(
        username="owner", password_hash=provider._password_hash,
        secret=provider._secret, state_path=provider._state_path,
    )
    assert migrated.verify_session(access_token=first.session.access_token) is not None
    assert migrated.begin_password_login(
        username="owner", password="correct-horse-battery-staple",
    ).stage == "verify"


def test_local_owner_reset_invalidates_session_and_requires_enrollment(provider):
    completion = _enroll(provider)
    provider.reset_factor_locally()
    assert provider.verify_session(access_token=completion.session.access_token) is None
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    assert challenge.stage == "enroll"


def test_logout_revoke_invalidates_server_side_session(provider):
    completion = _enroll(provider)
    provider.revoke_session(refresh_token=completion.session.refresh_token)
    assert provider.verify_session(access_token=completion.session.access_token) is None


def test_stale_logout_token_cannot_revoke_later_sessions(provider, monkeypatch):
    first = _enroll(provider).session
    provider.revoke_session(refresh_token=first.refresh_token)
    with provider._connect() as conn:
        encrypted = conn.execute("SELECT totp_secret FROM account WHERE id=1").fetchone()[0]
    later = time.time() + 60
    monkeypatch.setattr(totp_plugin.time, "time", lambda: later)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    current = provider.complete_totp_challenge(
        token=challenge.token,
        code=_totp_at(provider._decrypt(encrypted), int(later) // 30),
    ).session
    provider.revoke_session(refresh_token=first.refresh_token)
    assert provider.verify_session(access_token=current.access_token) is not None


def test_changed_state_secret_fails_closed(provider):
    _enroll(provider)
    with pytest.raises(Exception):
        TotpAuthProvider(
            username="owner", password_hash=provider._password_hash,
            secret=b"different-secret-material-32bytes!", state_path=provider._state_path,
        )


def test_owner_reset_cli_calls_local_only_reset(provider, monkeypatch, capsys):
    completion = _enroll(provider)
    monkeypatch.setattr(totp_plugin, "_build_provider", lambda: provider)
    assert totp_plugin.main(["reset", "--confirm"]) == 0
    assert "invalidated" in capsys.readouterr().out
    assert provider.verify_session(access_token=completion.session.access_token) is None


def test_documented_reset_module_entrypoint_resets_persistent_state(tmp_path):
    home = tmp_path / "home"
    secret = "R" * 32
    password_hash = hash_password("correct-horse-battery-staple")
    provider = TotpAuthProvider(
        username="owner", password_hash=password_hash, secret=secret.encode(),
        state_path=home / "dashboard-totp-auth.sqlite3",
    )
    session = _enroll(provider).session
    env = {
        **os.environ,
        "HERMES_HOME": str(home),
        "HERMES_DASHBOARD_TOTP_AUTH_USERNAME": "owner",
        "HERMES_DASHBOARD_TOTP_AUTH_PASSWORD_HASH": password_hash,
        "HERMES_DASHBOARD_TOTP_AUTH_SECRET": secret,
    }
    result = subprocess.run(
        [sys.executable, "-m", "plugins.dashboard_auth.totp", "reset", "--confirm"],
        cwd=str(Path(__file__).parents[3]), env=env, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "invalidated" in result.stdout
    assert provider.verify_session(access_token=session.access_token) is None


class _LegacyProvider(DashboardAuthProvider):
    name = "legacy"
    display_name = "Legacy"
    supports_password = True
    def start_login(self, **kwargs): raise NotImplementedError
    def complete_login(self, **kwargs): raise NotImplementedError
    def verify_session(self, **kwargs): return None
    def refresh_session(self, **kwargs): raise RefreshExpiredError
    def revoke_session(self, **kwargs): return None


def test_exclusive_totp_hides_legacy_interactive_provider(provider):
    clear_providers()
    try:
        register_provider(_LegacyProvider())
        register_provider(provider)
        assert [p.name for p in list_session_providers()] == ["totp"]
    finally:
        clear_providers()


def test_concurrent_initialization_serializes_fresh_state(tmp_path):
    state_path = tmp_path / "shared" / "totp.sqlite3"
    password_hash = hash_password("correct-horse-battery-staple")
    def build():
        return TotpAuthProvider(
            username="owner", password_hash=password_hash, secret=b"s" * 32,
            state_path=state_path,
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        providers = list(pool.map(lambda _n: build(), range(4)))
    assert len(providers) == 4
    conn = sqlite3.connect(state_path)
    assert conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 1


def test_plaintext_build_is_restart_stable_and_password_change_kills_session_and_challenge(
    tmp_path, monkeypatch,
):
    """The keyed plaintext fingerprint must not churn from a fresh scrypt salt."""
    config = {
        "username": "owner", "password": "  exact password  ", "secret": "z" * 32,
        "state_path": str(tmp_path / "state.sqlite3"),
    }
    monkeypatch.setattr(totp_plugin, "_config", lambda: config)
    first = totp_plugin._build_provider()
    initial = first.begin_password_login(username="owner", password="  exact password  ")
    session = first.complete_totp_challenge(token=initial.token, code=_pending_code(first)).session
    restarted = totp_plugin._build_provider()
    assert restarted.verify_session(access_token=session.access_token) is not None
    # A supplied password hash wins over plaintext and changing it invalidates
    # both previously issued sessions and pre-authentication challenges.
    stale = restarted.begin_password_login(username="owner", password="  exact password  ")
    config["password_hash"] = hash_password("new password with enough length")
    changed = totp_plugin._build_provider()
    assert changed.verify_session(access_token=session.access_token) is None
    with pytest.raises(InvalidCredentialsError):
        changed.challenge_details(stale.token)
    assert changed.begin_password_login(
        username="owner", password="new password with enough length"
    )


def test_plaintext_password_whitespace_is_exact_and_hash_precedence(tmp_path, monkeypatch):
    config = {
        "username": "owner", "password": " leading-and-trailing ", "secret": "w" * 32,
        "state_path": str(tmp_path / "plain.sqlite3"),
    }
    monkeypatch.setattr(totp_plugin, "_config", lambda: config)
    provider = totp_plugin._build_provider()
    assert provider.begin_password_login(username="owner", password=" leading-and-trailing ")
    with pytest.raises(InvalidCredentialsError):
        provider.begin_password_login(username="owner", password="leading-and-trailing")
    config["password_hash"] = hash_password("hash-wins-password")
    assert totp_plugin._build_provider().begin_password_login(
        username="owner", password="hash-wins-password"
    )


def test_reset_and_recovery_invalidate_old_pre_auth_challenges(provider):
    pending = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    provider.reset_factor_locally()
    with pytest.raises(InvalidCredentialsError):
        provider.challenge_details(pending.token)

    enrolled = _enroll(provider)
    old = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    reset = provider.recover_totp_challenge(token=old.token, recovery_code=enrolled.recovery_codes[0])
    with pytest.raises(InvalidCredentialsError):
        provider.challenge_details(old.token)
    assert provider.challenge_details(reset.token)["stage"] == "enroll"


def test_recovery_attempt_budget_persists_across_provider_reconstruction(provider):
    _enroll(provider)
    challenge = provider.begin_password_login(username="owner", password="correct-horse-battery-staple")
    for _ in range(5):
        fresh = TotpAuthProvider(
            username="owner", password_hash=provider._password_hash,
            secret=provider._secret, state_path=provider._state_path,
        )
        with pytest.raises(InvalidCredentialsError):
            fresh.recover_totp_challenge(token=challenge.token, recovery_code="BAD-CODE")
    with pytest.raises(InvalidCredentialsError, match="exhausted"):
        provider.recover_totp_challenge(token=challenge.token, recovery_code="BAD-CODE")


def test_malformed_explicit_provider_allowlist_has_no_legacy_fallback(monkeypatch):
    clear_providers()
    import hermes_cli.config
    monkeypatch.setattr(hermes_cli.config, "load_config", lambda: {"dashboard": {"auth_providers": "totp"}})
    try:
        register_provider(_LegacyProvider())
        assert list_session_providers() == []
    finally:
        clear_providers()

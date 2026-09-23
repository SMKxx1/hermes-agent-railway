"""Password + authenticator-app TOTP dashboard authentication.

This is intentionally a separate provider from ``basic``.  Basic is useful
for local, single-factor self-hosting; public deployments that load this
provider receive no session until both the configured password and an RFC 6238
code have been proved.  SQLite is the small shared state authority: it keeps
factor material encrypted at rest, recovery-code hashes, session versions, and
the last accepted TOTP time-step.  SQLite's write transaction makes a code
replay fail across workers sharing the same private volume.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hermes_cli.dashboard_auth import (
    DashboardAuthProvider, InvalidCredentialsError, LoginStart,
    RefreshExpiredError, Session,
)
from plugins.dashboard_auth.basic import (
    _SCRYPT_DKLEN, _SCRYPT_N, _SCRYPT_P, _SCRYPT_R, _SCRYPT_SALT_BYTES,
    _verify_password, hash_password,
)

logger = logging.getLogger(__name__)

_TTL = 12 * 60 * 60
_REFRESH_TTL = 30 * 24 * 60 * 60
_CHALLENGE_TTL = 5 * 60
_TOTP_STEP = 30
_TOTP_DIGITS = 6
_RECOVERY_COUNT = 8
_MAX_CHALLENGE_TRIES = 5
_SIG_LEN = hashlib.sha256().digest_size
LAST_SKIP_REASON = ""


@dataclass(frozen=True)
class TotpChallenge:
    token: str
    stage: str  # ``enroll`` or ``verify``


@dataclass(frozen=True)
class TotpCompletion:
    session: Session
    recovery_codes: tuple[str, ...] = ()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: dict, key: bytes) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _b64(raw + hmac.new(key, raw, hashlib.sha256).digest())


def _unsign(token: str, key: bytes) -> Optional[dict]:
    try:
        blob = _unb64(token)
        raw, signature = blob[:-_SIG_LEN], blob[-_SIG_LEN:]
        if not raw or not hmac.compare_digest(
            signature, hmac.new(key, raw, hashlib.sha256).digest()
        ):
            return None
        return json.loads(raw)
    except Exception:
        return None


def _totp_at(secret: bytes, counter: int) -> str:
    digest = hmac.new(secret, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10 ** _TOTP_DIGITS)).zfill(_TOTP_DIGITS)


def _normalise_code(code: str) -> str:
    return "".join(ch for ch in code if ch.isdigit())


class TotpAuthProvider(DashboardAuthProvider):
    name = "totp"
    display_name = "Username, Password & Authenticator"
    supports_password = True
    # Registry treats this as a deployment policy, not merely a login option.
    exclusive_session_provider = True

    def __init__(
        self, *, username: str, password_hash: str, secret: bytes,
        state_path: Path, issuer: str = "Hermes Agent", ttl_seconds: int = _TTL,
        credential_fingerprint: str | None = None,
    ) -> None:
        if not username or not password_hash:
            raise ValueError("username and password_hash are required")
        if len(secret) < 32:
            raise ValueError("secret must be at least 32 bytes")
        self._username = username
        self._password_hash = password_hash
        self._secret = secret
        self._session_key = hmac.new(secret, b"session-v1", hashlib.sha256).digest()
        self._challenge_key = hmac.new(secret, b"challenge-v1", hashlib.sha256).digest()
        self._recovery_key = hmac.new(secret, b"recovery-v1", hashlib.sha256).digest()
        self._cipher = AESGCM(hmac.new(secret, b"factor-v1", hashlib.sha256).digest())
        self._state_path = Path(state_path)
        self._issuer = issuer.strip() or "Hermes Agent"
        self._ttl = max(60, int(ttl_seconds))
        self._credential_fingerprint = credential_fingerprint
        self._init_state()

    # OAuth protocol methods are deliberately unavailable.
    def start_login(self, *, redirect_uri: str) -> LoginStart:
        raise NotImplementedError("TOTP provider is password-only")

    def complete_login(self, **kwargs) -> Session:
        raise NotImplementedError("TOTP provider is password-only")

    def complete_password_login(self, *, username: str, password: str) -> Session:
        # The generic route detects begin_password_login; retaining this guard
        # prevents a future caller from accidentally minting a 1FA session.
        raise InvalidCredentialsError("second-factor challenge required")

    def begin_password_login(self, *, username: str, password: str) -> TotpChallenge:
        username_ok = hmac.compare_digest(username.encode(), self._username.encode())
        # Always execute scrypt, including for a bad username.
        verified = _verify_password(password, self._password_hash if username_ok else _DUMMY_HASH)
        if not (username_ok and verified):
            raise InvalidCredentialsError("invalid username or password")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT totp_secret,recovery_pending FROM account WHERE id=1").fetchone()
            if row and row["recovery_pending"]:
                # Consuming a recovery code authorizes only that browser's
                # enrollment challenge. The factor is temporarily absent, but
                # a password-only caller must not claim a replacement factor.
                # Keep the recovery form available: another unused recovery
                # code can restart an expired or abandoned enrollment.
                return self._new_challenge(conn, "verify")
            stage = "verify" if row and row[0] else "enroll"
            if stage == "enroll":
                # One pending enrollment globally.  A newer successful
                # password verification replaces it, invalidating any stale
                # browser's challenge rather than sharing a mutable secret.
                conn.execute("DELETE FROM challenge WHERE stage='enroll'")
                pending = self._encrypt(secrets.token_bytes(20))
                conn.execute("UPDATE account SET pending_secret=? WHERE id=1", (pending,))
            return self._new_challenge(conn, stage)

    def challenge_details(self, token: str) -> dict[str, str]:
        with self._connect() as conn:
            row = self._challenge_row(conn, token)
            if row is None:
                raise InvalidCredentialsError("challenge expired")
            result = {"stage": row["stage"]}
            if row["stage"] == "enroll":
                account = conn.execute("SELECT pending_secret FROM account WHERE id=1").fetchone()
                if not account or not account[0]:
                    raise InvalidCredentialsError("challenge expired")
                secret = self._decrypt(account[0])
                label = quote(f"{self._issuer}:{self._username}", safe="")
                issuer = quote(self._issuer, safe="")
                result["otpauth_uri"] = (
                    f"otpauth://totp/{label}?secret={base64.b32encode(secret).decode().rstrip('=')}"
                    f"&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
                )
            return result

    def complete_totp_challenge(self, *, token: str, code: str) -> TotpCompletion:
        code = _normalise_code(code)
        if len(code) != _TOTP_DIGITS:
            raise InvalidCredentialsError("invalid authenticator code")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._challenge_row(conn, token)
            if row is None:
                raise InvalidCredentialsError("challenge expired")
            if row["tries"] >= _MAX_CHALLENGE_TRIES:
                raise InvalidCredentialsError("challenge exhausted")
            account = conn.execute(
                "SELECT totp_secret,pending_secret,session_version,last_counter FROM account WHERE id=1"
            ).fetchone()
            encrypted = account["pending_secret"] if row["stage"] == "enroll" else account["totp_secret"]
            if not encrypted:
                raise InvalidCredentialsError("challenge expired")
            counter = self._matching_counter(self._decrypt(encrypted), code)
            if counter is None or counter <= account["last_counter"]:
                conn.execute("UPDATE challenge SET tries=tries+1 WHERE digest=?", (row["digest"],))
                conn.commit()  # retain bounded-attempt accounting on failure
                raise InvalidCredentialsError("invalid authenticator code")
            # Both conditions make acceptance one-shot even when two workers
            # race with the same RFC 6238 code.
            updated = conn.execute(
                "UPDATE account SET last_counter=? WHERE id=1 AND last_counter < ?",
                (counter, counter),
            ).rowcount
            if updated != 1 or conn.execute(
                "UPDATE challenge SET used=1 WHERE digest=? AND used=0", (row["digest"],)
            ).rowcount != 1:
                raise InvalidCredentialsError("authenticator code already used")
            recovery: tuple[str, ...] = ()
            if row["stage"] == "enroll":
                conn.execute(
                    "UPDATE account SET totp_secret=pending_secret,pending_secret=NULL,recovery_pending=0,session_version=session_version+1 WHERE id=1"
                )
                conn.execute("DELETE FROM challenge")
                account = conn.execute("SELECT session_version FROM account WHERE id=1").fetchone()
                recovery = tuple(self._replace_recovery_codes(conn))
            return TotpCompletion(
                session=self._mint_session(account["session_version"]), recovery_codes=recovery
            )

    def recover_totp_challenge(self, *, token: str, recovery_code: str) -> TotpChallenge:
        digest = hmac.new(self._recovery_key, recovery_code.strip().upper().encode(), hashlib.sha256).digest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._challenge_row(conn, token)
            if row is None or row["stage"] != "verify":
                raise InvalidCredentialsError("challenge expired")
            if row["tries"] >= _MAX_CHALLENGE_TRIES:
                raise InvalidCredentialsError("challenge exhausted")
            used = conn.execute(
                "DELETE FROM recovery_code WHERE digest=?", (digest,)
            ).rowcount
            if used != 1:
                conn.execute("UPDATE challenge SET tries=tries+1 WHERE digest=?", (row["digest"],))
                conn.commit()
                raise InvalidCredentialsError("invalid recovery code")
            # A recovery code is an emergency factor reset. Existing tokens
            # and the previous factor die now. Other unused codes remain
            # available to restart an abandoned enrollment; completing the
            # replacement factor rotates the entire recovery-code set.
            conn.execute(
                "UPDATE account SET totp_secret=NULL,pending_secret=?,recovery_pending=1,last_counter=-1,session_version=session_version+1 WHERE id=1",
                (self._encrypt(secrets.token_bytes(20)),),
            )
            conn.execute("DELETE FROM challenge")
            return self._new_challenge(conn, "enroll")

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        payload = _unsign(access_token, self._session_key)
        if not payload or payload.get("kind") != "access" or payload.get("exp", 0) <= int(time.time()):
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT session_version,totp_secret FROM account WHERE id=1").fetchone()
        if not row or not row["totp_secret"] or row["session_version"] != payload.get("version"):
            return None
        return self._session_from_payload(access_token, payload)

    def refresh_session(self, *, refresh_token: str) -> Session:
        payload = _unsign(refresh_token, self._session_key)
        if not payload or payload.get("kind") != "refresh" or payload.get("exp", 0) <= int(time.time()):
            raise RefreshExpiredError("refresh token expired or invalid")
        with self._connect() as conn:
            row = conn.execute("SELECT session_version,totp_secret FROM account WHERE id=1").fetchone()
        if not row or not row["totp_secret"] or row["session_version"] != payload.get("version"):
            raise RefreshExpiredError("session revoked")
        return self._mint_session(row["session_version"])

    def revoke_session(self, *, refresh_token: str) -> None:
        # This provider represents one configured dashboard owner.  A logout
        # is therefore a real server-side revocation, not merely cookie
        # deletion: bumping the shared version rejects the access/refresh
        # tokens on every worker immediately.
        if not refresh_token:
            return None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            payload = _unsign(refresh_token, self._session_key)
            if (
                payload and payload.get("kind") == "refresh"
                and payload.get("sub") == self._username
                and payload.get("exp", 0) > int(time.time())
            ):
                # A previously revoked or expired token has no authority over
                # a later login. Make logout idempotent across workers rather
                # than allowing one captured old token to log the owner out
                # indefinitely, even after password rotation or factor reset.
                conn.execute(
                    "UPDATE account SET session_version=session_version+1 WHERE id=1 AND session_version=?",
                    (payload.get("version"),),
                )

    def reset_factor_locally(self) -> None:
        """Owner-SSH recovery API: invalidate all sessions and require enrollment.

        Intended for a local-only CLI command which constructs this provider
        from the deployment's credentials.  It never returns a factor secret
        or recovery code and cannot be reached by HTTP.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM recovery_code")
            conn.execute("DELETE FROM challenge")
            conn.execute(
                "UPDATE account SET totp_secret=NULL,pending_secret=NULL,recovery_pending=0,last_counter=-1,session_version=session_version+1 WHERE id=1"
            )

    # ---- SQLite/state helpers --------------------------------------------
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self._state_path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _enable_wal(self) -> None:
        """Enable WAL once per provider initialization, retrying startup races.

        ``journal_mode=WAL`` itself needs an exclusive SQLite lock.  It must
        not run on every request connection, and concurrent gateway/dashboard
        initialization may legitimately contend while a fresh volume is first
        opened.
        """
        last_error: Exception | None = None
        for _ in range(40):
            conn = sqlite3.connect(str(self._state_path), timeout=1, isolation_level=None)
            try:
                conn.execute("PRAGMA busy_timeout=1000")
                conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                time.sleep(0.05)
            finally:
                conn.close()
        raise sqlite3.OperationalError("unable to enable SQLite WAL mode") from last_error

    def _init_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._state_path.exists():
            # Owner-only state; chmod is also applied below for existing files.
            self._state_path.touch(mode=0o600)
        try:
            os.chmod(self._state_path, 0o600)
        except OSError:
            pass
        self._enable_wal()
        credential_fp = self._credential_fingerprint or hashlib.sha256(
            self._username.encode() + b"\0" + self._password_hash.encode()
        ).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS account (
                id INTEGER PRIMARY KEY CHECK(id=1), totp_secret BLOB,
                pending_secret BLOB, session_version INTEGER NOT NULL,
                last_counter INTEGER NOT NULL, credential_fingerprint TEXT NOT NULL,
                recovery_pending INTEGER NOT NULL DEFAULT 0)""")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(account)")}
            if "recovery_pending" not in columns:
                conn.execute("ALTER TABLE account ADD COLUMN recovery_pending INTEGER NOT NULL DEFAULT 0")
                # Preserve pre-upgrade pending recovery challenges without
                # reopening enrollment to password-only callers. First-time
                # setup has version 1 and remains unchanged.
                conn.execute(
                    "UPDATE account SET recovery_pending=1 WHERE totp_secret IS NULL AND pending_secret IS NOT NULL AND session_version>1"
                )
            conn.execute("""CREATE TABLE IF NOT EXISTS challenge (
                digest BLOB PRIMARY KEY, stage TEXT NOT NULL, expires INTEGER NOT NULL,
                tries INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("CREATE TABLE IF NOT EXISTS recovery_code (digest BLOB PRIMARY KEY)")
            row = conn.execute("SELECT credential_fingerprint FROM account WHERE id=1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO account(id,session_version,last_counter,credential_fingerprint) VALUES(1,1,-1,?)",
                    (credential_fp,),
                )
            elif not hmac.compare_digest(row["credential_fingerprint"], credential_fp):
                conn.execute(
                    "UPDATE account SET credential_fingerprint=?,session_version=session_version+1 WHERE id=1",
                    (credential_fp,),
                )
                conn.execute("DELETE FROM challenge")
            # A changed state-encryption key must never silently create a new
            # factor or accept an unreadable old one.  Registration fails
            # closed, preserving the existing encrypted state for the owner to
            # recover with the original secret or reset locally.
            current = conn.execute("SELECT totp_secret,pending_secret FROM account WHERE id=1").fetchone()
            for encrypted in (current["totp_secret"], current["pending_secret"]):
                if encrypted:
                    self._decrypt(encrypted)
            conn.execute("DELETE FROM challenge WHERE expires < ? OR used=1", (int(time.time()),))

    def _encrypt(self, value: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._cipher.encrypt(nonce, value, b"hermes-totp-v1")

    def _decrypt(self, value: bytes) -> bytes:
        return self._cipher.decrypt(value[:12], value[12:], b"hermes-totp-v1")

    def _new_challenge(self, conn: sqlite3.Connection, stage: str) -> TotpChallenge:
        token = _b64(secrets.token_bytes(32))
        digest = hmac.new(self._challenge_key, token.encode(), hashlib.sha256).digest()
        conn.execute("DELETE FROM challenge WHERE expires < ? OR used=1", (int(time.time()),))
        conn.execute(
            "INSERT INTO challenge(digest,stage,expires) VALUES(?,?,?)",
            (digest, stage, int(time.time()) + _CHALLENGE_TTL),
        )
        return TotpChallenge(token=token, stage=stage)

    def _challenge_row(self, conn: sqlite3.Connection, token: str):
        digest = hmac.new(self._challenge_key, token.encode(), hashlib.sha256).digest()
        return conn.execute(
            "SELECT digest,stage,expires,tries,used FROM challenge WHERE digest=? AND expires>=? AND used=0",
            (digest, int(time.time())),
        ).fetchone()

    def _matching_counter(self, secret: bytes, code: str) -> Optional[int]:
        current = int(time.time()) // _TOTP_STEP
        for counter in (current - 1, current, current + 1):
            if hmac.compare_digest(_totp_at(secret, counter), code):
                return counter
        return None

    def _replace_recovery_codes(self, conn: sqlite3.Connection) -> list[str]:
        conn.execute("DELETE FROM recovery_code")
        codes = [f"{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}" for _ in range(_RECOVERY_COUNT)]
        conn.executemany(
            "INSERT INTO recovery_code(digest) VALUES(?)",
            [(hmac.new(self._recovery_key, code.encode(), hashlib.sha256).digest(),) for code in codes],
        )
        return codes

    def _mint_session(self, version: int) -> Session:
        now = int(time.time())
        access = _sign({"sub": self._username, "kind": "access", "version": version, "exp": now + self._ttl}, self._session_key)
        refresh = _sign({"sub": self._username, "kind": "refresh", "version": version, "exp": now + _REFRESH_TTL}, self._session_key)
        return Session(self._username, "", self._username, "", self.name, now + self._ttl, access, refresh)

    def _session_from_payload(self, token: str, payload: dict) -> Session:
        return Session(self._username, "", self._username, "", self.name, int(payload["exp"]), token, "")


_DUMMY_HASH = hash_password("dummy-password-for-totp-provider")


def _config() -> dict:
    try:
        from hermes_cli.config import cfg_get, load_config
        value = cfg_get(load_config(), "dashboard", "totp_auth", default={})
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _resolve(env: str, section: dict, key: str, *, strip: bool = True) -> str:
    from hermes_cli.managed_scope import load_managed_env

    managed = load_managed_env()
    if env in managed:
        # Empty managed values are deliberate tombstones. Falling back to a
        # writable config hash here would resurrect the old password when a
        # Railway operator switches from a hash to a plaintext credential.
        value = managed[env]
        return str(value).strip() if strip else str(value)
    value = os.environ.get(env)
    if value is None or (not value if not strip else not value.strip()):
        value = section.get(key, "") or ""
    return str(value).strip() if strip else str(value)


def _secret(raw: str) -> bytes:
    try:
        decoded = _unb64(raw)
        if len(decoded) >= 32:
            return decoded
    except Exception:
        pass
    return raw.encode()


def _valid_password_hash(value: str) -> bool:
    """Accept only the bounded scrypt format emitted by ``hash_password``."""
    try:
        scheme, n, r, p, salt, digest = value.split("$")
        return (
            scheme == "scrypt" and int(n) == _SCRYPT_N and int(r) == _SCRYPT_R
            and int(p) == _SCRYPT_P and len(base64.b64decode(salt)) == _SCRYPT_SALT_BYTES
            and len(base64.b64decode(digest)) == _SCRYPT_DKLEN
        )
    except (ValueError, TypeError):
        return False


def _build_provider() -> TotpAuthProvider:
    section = _config()
    username = _resolve("HERMES_DASHBOARD_TOTP_AUTH_USERNAME", section, "username")
    password_hash = _resolve("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD_HASH", section, "password_hash")
    plaintext = _resolve("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD", section, "password", strip=False)
    raw_secret = _resolve("HERMES_DASHBOARD_TOTP_AUTH_SECRET", section, "secret")
    if plaintext and len(plaintext) < 12:
        raise ValueError("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD must be at least 12 characters")
    using_plaintext = bool(plaintext and not password_hash)
    if using_plaintext:
        password_hash = hash_password(plaintext)
    if not username or not password_hash or not raw_secret:
        raise ValueError("dashboard.totp_auth requires username, password_hash (or password), and a stable secret")
    if not _valid_password_hash(password_hash):
        raise ValueError("password_hash must be a valid Hermes scrypt hash (use `python -m plugins.dashboard_auth.totp hash-password`)")
    state_raw = str(section.get("state_path", "") or "").strip()
    if state_raw:
        state_path = Path(state_raw).expanduser()
    else:
        from hermes_constants import get_hermes_home
        state_path = get_hermes_home() / "dashboard-totp-auth.sqlite3"
    secret = _secret(raw_secret)
    credential_fingerprint = (
        hmac.new(secret, username.encode() + b"\0" + plaintext.encode(), hashlib.sha256).hexdigest()
        if using_plaintext else None
    )
    provider = TotpAuthProvider(
        username=username, password_hash=password_hash, secret=_secret(raw_secret),
        state_path=state_path, issuer=str(section.get("issuer", "Hermes Agent")),
        ttl_seconds=int(section.get("session_ttl_seconds", _TTL) or _TTL),
        credential_fingerprint=credential_fingerprint,
    )
    return provider


def register(ctx) -> None:
    global LAST_SKIP_REASON
    LAST_SKIP_REASON = ""
    try:
        provider = _build_provider()
    except Exception as exc:
        LAST_SKIP_REASON = f"TOTP provider construction failed: {exc}"
        logger.warning("dashboard-auth-totp: %s", LAST_SKIP_REASON)
        return
    ctx.register_dashboard_auth_provider(provider)
    logger.info("dashboard-auth-totp: registered exclusive MFA provider")


def main(argv: Optional[list[str]] = None) -> int:
    """Owner-SSH recovery utility; intentionally has no network surface.

    ``python -m plugins.dashboard_auth.totp reset --confirm`` reads the exact
    production config/env resolution used by ``register()``.  The explicit
    confirmation prevents an accidental terminal invocation from revoking the
    current dashboard owner.  The next successful password login starts fresh
    authenticator enrollment.
    """
    import argparse
    import getpass

    from hermes_cli.env_loader import load_hermes_dotenv
    load_hermes_dotenv()

    parser = argparse.ArgumentParser(description="Local Hermes dashboard TOTP recovery")
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset", help="invalidate factor, recovery codes, and all sessions")
    reset.add_argument("--confirm", action="store_true", help="perform the destructive local reset")
    hp = sub.add_parser("hash-password", help="print a scrypt hash for dashboard.totp_auth.password_hash")
    args = parser.parse_args(argv)
    if args.command == "hash-password":
        password = getpass.getpass("New dashboard password: ")
        confirm = getpass.getpass("Confirm password: ")
        if len(password) < 12:
            parser.error("password must be at least 12 characters")
        if not hmac.compare_digest(password, confirm):
            parser.error("passwords do not match")
        print(hash_password(password))
        return 0
    if not args.confirm:
        parser.error("reset requires --confirm")
    _build_provider().reset_factor_locally()
    print("Dashboard TOTP factor reset. All sessions and recovery codes were invalidated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

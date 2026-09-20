"""Initialize a fresh Railway volume and enforce operator-owned configuration.

Only the container's pre-service hook invokes main(). Tests call bootstrap()
with temporary directories. No credential is baked into source or the image.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
from typing import Mapping

import yaml
from dotenv import dotenv_values


class BootstrapError(ValueError):
    """Actionable setup problem, safe to display without secret values."""


# These belong to Railway even when empty: removing a variable must not revive
# an old copy in the volume's .env. Additional supplied provider credentials
# matching the suffixes below are included too.
MANAGED_KEYS = frozenset({
    "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "FIRECRAWL_API_KEY", "EXA_API_KEY",
    "ELEVENLABS_API_KEY", "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "TELEGRAM_ALLOWED_USERS",
    "TELEGRAM_ALLOW_ALL_USERS", "TELEGRAM_HOME_CHANNEL",
    "TELEGRAM_HOME_CHANNEL_THREAD_ID", "WHATSAPP_ALLOWED_USERS",
    "WHATSAPP_ALLOW_ALL_USERS", "WHATSAPP_ENABLED", "WHATSAPP_MODE",
    "WHATSAPP_DM_POLICY", "WHATSAPP_HOME_CHANNEL",
    "WHATSAPP_HOME_CHANNEL_THREAD_ID", "HERMES_DASHBOARD_TOTP_AUTH_USERNAME",
    "HERMES_DASHBOARD_TOTP_AUTH_PASSWORD",
    "HERMES_DASHBOARD_TOTP_AUTH_PASSWORD_HASH",
})
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_CLIENT_SECRET")
_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _safe_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise BootstrapError(f"Refusing symlink in bootstrap path: {part.name}")
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise BootstrapError(f"Unsupported bootstrap file type: {path.name}")
    if path.is_file() and path.stat().st_nlink != 1:
        raise BootstrapError(f"Refusing hard-linked bootstrap file: {path.name}")


def _permissions(path: Path, mode: int, uid: int | None, gid: int | None) -> None:
    os.chmod(path, mode)
    if uid is not None and gid is not None:
        os.chown(path, uid, gid)


def _seed(path: Path, content: str, *, uid: int | None, gid: int | None) -> bool:
    _safe_path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        if not path.is_file():
            raise BootstrapError(f"Expected regular file: {path.name}") from None
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    _permissions(path, 0o600, uid, gid)
    return True


def _replace(path: Path, content: str, *, gid: int | None) -> None:
    _safe_path(path)
    fd, name = tempfile.mkstemp(prefix=".bootstrap-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _permissions(temp, 0o640, 0 if os.geteuid() == 0 else None, gid)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _dotenv(values: Mapping[str, str]) -> str:
    # python-dotenv accepts single-quoted values and escaped quotes/backslashes.
    # Reject multiline values so a credential cannot become another assignment.
    lines = []
    for key, value in sorted(values.items()):
        if not _NAME.fullmatch(key) or any(c in value for c in "\x00\r\n"):
            raise BootstrapError(f"Invalid single-line setting: {key}")
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{key}='{escaped}'")
    return "\n".join(lines) + "\n"


def bootstrap(
    home: Path,
    managed_dir: Path,
    seed_path: Path,
    environ: Mapping[str, str],
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> dict:
    """Seed missing instance files and refresh the ephemeral Railway overlay."""
    if not home.is_absolute() or not managed_dir.is_absolute():
        raise BootstrapError("HERMES_HOME and managed directory must be absolute")
    username = environ.get("HERMES_DASHBOARD_TOTP_AUTH_USERNAME", "").strip()
    password = environ.get("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD", "")
    password_hash = environ.get("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD_HASH", "")
    if not username or len(username) > 128:
        raise BootstrapError("Set HERMES_DASHBOARD_TOTP_AUTH_USERNAME (1–128 characters)")
    if password_hash:
        if not password_hash.startswith("scrypt$"):
            raise BootstrapError("HERMES_DASHBOARD_TOTP_AUTH_PASSWORD_HASH must be a scrypt hash")
    elif len(password) < 12 or len(password) > 1024:
        raise BootstrapError("Set HERMES_DASHBOARD_TOTP_AUTH_PASSWORD (12–1024 characters)")

    for path in (home, managed_dir):
        _safe_path(path)
        path.mkdir(parents=True, exist_ok=True)
    _permissions(home, 0o700, uid, gid)
    _permissions(managed_dir, 0o750, 0 if os.geteuid() == 0 else None, gid)

    config = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    config["terminal"]["cwd"] = str(home / "workspace")
    seeded = _seed(home / "config.yaml", yaml.safe_dump(config, sort_keys=False), uid=uid, gid=gid)
    _seed(home / ".env", "# Instance-generated credentials only. Supply provider keys in Railway.\n", uid=uid, gid=gid)
    _safe_path(home / ".env")
    values = dotenv_values(home / ".env", interpolate=False)

    # Persist generated secrets exactly once. Do not copy operator-supplied
    # secrets to the volume; their next Railway rotation remains authoritative.
    additions: dict[str, str] = {}
    for key in ("API_SERVER_KEY", "HERMES_DASHBOARD_TOTP_AUTH_SECRET"):
        supplied = environ.get(key, "")
        persisted = values.get(key) or ""
        if supplied and len(supplied) < 32:
            raise BootstrapError(f"{key} must contain at least 32 characters")
        if not supplied and not persisted:
            additions[key] = secrets.token_urlsafe(48)
        elif not supplied and len(persisted) < 32:
            raise BootstrapError(f"Stored {key} is too short; replace it before restarting")
    if additions:
        fd = os.open(home / ".env", os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n" + _dotenv(additions))
            stream.flush()
            os.fsync(stream.fileno())
    _permissions(home / ".env", 0o600, uid, gid)

    keys = set(MANAGED_KEYS)
    keys.update(k for k in environ if not k.startswith("RAILWAY_") and k.endswith(_CREDENTIAL_SUFFIXES))
    # Remember names, never values, so removing an additional provider key from
    # Railway cannot resurrect a stale user copy after container replacement.
    inventory_path = home / ".railway-managed-keys.json"
    _safe_path(inventory_path)
    if inventory_path.exists():
        old = json.loads(inventory_path.read_text(encoding="utf-8"))
        if not isinstance(old, list) or any(not isinstance(k, str) or not _NAME.fullmatch(k) for k in old):
            raise BootstrapError("Invalid managed-key inventory")
        keys.update(old)
    # Generated internal credentials fall back to this instance's volume when
    # the operator removes an explicit override; they are not external keys.
    keys.difference_update({"API_SERVER_KEY", "HERMES_DASHBOARD_TOTP_AUTH_SECRET"})
    inventory_keys = sorted(keys)
    for key in ("API_SERVER_KEY", "HERMES_DASHBOARD_TOTP_AUTH_SECRET"):
        if environ.get(key):
            keys.add(key)
    managed_values = {k: environ.get(k, "") for k in keys}
    # These are wiring/security invariants, not per-user app preferences.
    managed_values["API_SERVER_HOST"] = "127.0.0.1"
    public_url = environ.get("HERMES_DASHBOARD_PUBLIC_URL", "").strip()
    if not public_url and environ.get("RAILWAY_PUBLIC_DOMAIN"):
        public_url = "https://" + environ["RAILWAY_PUBLIC_DOMAIN"].strip()
    if public_url:
        from urllib.parse import urlsplit
        parsed = urlsplit(public_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BootstrapError("HERMES_DASHBOARD_PUBLIC_URL must be an absolute HTTP(S) URL without credentials")
        managed_values["HERMES_DASHBOARD_PUBLIC_URL"] = public_url.rstrip("/")
    _replace(managed_dir / ".env", _dotenv(managed_values), gid=gid)
    _replace(managed_dir / "config.yaml", yaml.safe_dump({
        "dashboard": {"auth_providers": ["totp"]},
        "platforms": {"api_server": {"extra": {"host": "127.0.0.1"}}},
    }, sort_keys=False), gid=gid)
    # Atomic inventory replacement is owned by the instance user.
    _replace(inventory_path, json.dumps(inventory_keys) + "\n", gid=gid)
    _permissions(inventory_path, 0o600, uid, gid)
    collisions = sorted(k for k in keys if values.get(k))
    return {"config_seeded": seeded, "managed_keys": len(keys), "overridden_names": collisions}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("HERMES_HOME", "/opt/data")))
    parser.add_argument("--managed-dir", type=Path, default=Path("/etc/hermes"))
    args = parser.parse_args()
    try:
        import pwd
        account = pwd.getpwnam("hermes")
        # Stage2 applies UID/GID remapping after this hook. Pre-own both the
        # volume and managed overlay to the final IDs, including /etc/hermes.
        def target_id(primary: str, alias: str, default: int) -> int:
            raw = os.environ.get(primary) or os.environ.get(alias) or str(default)
            if not raw.isdecimal() or not 1 <= int(raw) <= 65534:
                raise BootstrapError(f"{primary} must be a non-root numeric ID")
            return int(raw)
        result = bootstrap(args.home, args.managed_dir, Path(__file__).resolve().parents[1] / "deploy/railway/defaults.yaml", dict(os.environ), uid=target_id("HERMES_UID", "PUID", account.pw_uid), gid=target_id("HERMES_GID", "PGID", account.pw_gid))
        print("[railway] Configuration ready; password + authenticator required.")
        if result["overridden_names"]:
            print("[railway] Railway owns these settings; stored copies are ignored: " + ", ".join(result["overridden_names"]))
        return 0
    except (BootstrapError, OSError, ValueError) as exc:
        # Only BootstrapError messages are specifically designed to omit values.
        print("[railway] " + (str(exc) if isinstance(exc, BootstrapError) else "Cannot initialize configuration; check volume and file permissions."), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

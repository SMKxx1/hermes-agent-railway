"""Origin checks shared by dashboard cookie-authenticated write paths."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def _origin(value: str, *, allow_path: bool = False) -> tuple[str, str, int] | None:
    """Parse a web origin, allowing an operator public URL's path prefix."""
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (not allow_path and parsed.path not in {"", "/"})
        ):
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname, port
    except ValueError:
        return None


def require_same_origin(request: Request) -> None:
    """Reject cross-origin browser writes, including same-site sibling hosts.

    SameSite cookies still accompany requests from a hostile sibling origin.
    Form POSTs do not need CORS preflight, so enforce this before any side
    effect or cookie refresh. Native clients without browser origin/fetch
    metadata remain supported; bearer-token authentication does not use this
    cookie CSRF check.
    """
    origin = request.headers.get("origin")
    if origin is None:
        # Older/native clients may omit Origin. Modern browser requests from
        # another origin must still be rejected if a proxy removed it.
        if request.headers.get("sec-fetch-site", "").lower() in {"same-site", "cross-site"}:
            raise HTTPException(status_code=403, detail="Cross-origin request rejected")
        return

    from hermes_cli.dashboard_auth.prefix import resolve_public_url

    expected = _origin(resolve_public_url() or str(request.base_url), allow_path=True)
    actual = _origin(origin)
    if expected is None or actual is None or actual != expected:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")

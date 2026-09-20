"""Local-only entry point for ``python -m plugins.dashboard_auth.totp``."""
from . import main


if __name__ == "__main__":
    raise SystemExit(main())

"""Profile-scoped, interprocess-safe config revisions and writes."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from hermes_constants import get_hermes_home


class ConfigLockTimeout(RuntimeError):
    """Another process is currently writing this profile's configuration."""


_LOCAL = threading.local()


@contextmanager
def config_transaction(timeout_seconds: float = 10.0) -> Iterator[None]:
    """Acquire an advisory lock stored inside the active profile's state root."""
    depth = int(getattr(_LOCAL, "depth", 0))
    if depth:
        _LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _LOCAL.depth -= 1
        return

    lock_path = get_hermes_home() / "config.lock"
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(lock_path.parent, 0o700)
    except OSError:
        pass
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(lock_path, 0o600)
    except OSError:
        pass
    handle = os.fdopen(fd, "a+", encoding="utf-8")
    acquired = False
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise ConfigLockTimeout("Timed out waiting for the profile config lock")
                time.sleep(0.05)
        _LOCAL.depth = 1
        try:
            yield
        finally:
            _LOCAL.depth = 0
    finally:
        try:
            if not acquired:
                pass
            elif os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

#!/usr/bin/env python3
"""Audit application and PM uv registry pins, including optional/platform pins.

Run with a Python environment containing pip-audit. No project dependencies or
project code are installed. uv export alone omits packages for other platforms;
pip-audit rejects duplicate names, so alternate locked versions get a separate
batch. Every registry entry is checked without resolving a replacement version.
Non-registry sources fail the check after registry pins have been audited; their
declared versions do not prove equivalence to the corresponding PyPI releases.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def audit_lock(lockfile: Path) -> int:
    packages = tomllib.loads(lockfile.read_text())["package"]
    batches: list[dict[str, str]] = []
    unsupported: list[str] = []
    for package in packages:
        if "registry" not in package.get("source", {}):
            if package.get("source") in ({"editable": "."}, {"virtual": "."}):
                continue  # The application or PM project itself.
            unsupported.append(
                f"{package['name']}=={package['version']}: {package.get('source', {})}"
            )
            continue
        name, version = package["name"], package["version"]
        batch = next((b for b in batches if name not in b), None)
        if batch is None:
            batch = {}
            batches.append(batch)
        batch[name] = version

    status = 0
    with tempfile.TemporaryDirectory(prefix="hermes-audit-") as temporary:
        for index, batch in enumerate(batches, start=1):
            requirements = Path(temporary) / f"requirements-{index}.txt"
            requirements.write_text("".join(f"{name}=={version}\n" for name, version in sorted(batch.items())))
            print(
                f"{lockfile}: auditing batch {index}/{len(batches)}: {len(batch)} locked versions",
                flush=True,
            )
            result = subprocess.run([
                sys.executable, "-m", "pip_audit", "--disable-pip", "--no-deps",
                "--progress-spinner", "off", "--requirement", str(requirements),
            ], check=False)
            status = max(status, result.returncode)
    if unsupported:
        print(f"{lockfile}: cannot audit non-registry dependencies:", file=sys.stderr)
        for dependency in unsupported:
            print(f"  {dependency}", file=sys.stderr)
        status = max(status, 2)
    return status


def main(root: Path | None = None) -> int:
    if root is None:
        root = Path(__file__).resolve().parents[2]
    return max(audit_lock(root / lockfile) for lockfile in ("uv.lock", "pm/uv.lock"))


if __name__ == "__main__":
    raise SystemExit(main())

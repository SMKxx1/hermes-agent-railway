#!/usr/bin/env python3
"""Audit all uv registry pins, including optional/platform-specific versions.

Run with a Python environment containing pip-audit. No project dependencies or
project code are installed. uv export alone omits packages for other platforms;
pip-audit rejects duplicate names, so alternate locked versions get a separate
batch. Every registry entry is checked without resolving a replacement version.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    batches: list[dict[str, str]] = []
    for package in packages:
        if "registry" not in package.get("source", {}):
            if package.get("source") == {"editable": "."}:
                continue  # The project itself is checked by regression tests.
            raise ValueError(f"Cannot audit non-registry dependency: {package['name']}")
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
            print(f"Auditing batch {index}/{len(batches)}: {len(batch)} locked versions", flush=True)
            result = subprocess.run([
                sys.executable, "-m", "pip_audit", "--disable-pip", "--no-deps",
                "--progress-spinner", "off", "--requirement", str(requirements),
            ], check=False)
            status = max(status, result.returncode)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

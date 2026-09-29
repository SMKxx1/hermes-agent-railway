#!/usr/bin/env python3
"""Audit application and PM uv pins, including optional/platform pins.

Run with a Python environment containing pip-audit. No project dependencies or
project code are installed. uv export alone omits packages for other platforms;
pip-audit rejects duplicate names, so alternate locked versions get a separate
batch. Every registry entry is checked without resolving a replacement version.
Reviewed non-registry pins are checked by source commit and all upstream-package
advisories in OSV. Their versions are never passed off as equivalent PyPI releases.
Unknown source identities and unreviewed advisory records fail closed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path


SOURCE_REVIEWS = Path(__file__).with_name("python_source_reviews.json")
OSV_QUERY_URL = "https://api.osv.dev/v1/query"


def advisory_digest(advisory: dict) -> str:
    """Bind a finding review to its complete contents, including affected ranges."""
    encoded = json.dumps(advisory, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_identity(package: dict) -> dict:
    return {key: package[key] for key in ("name", "version", "source", "wheels", "sdist") if key in package}


def osv_advisories(query: dict) -> list[dict]:
    advisories = []
    pages = set()
    while True:
        request = urllib.request.Request(
            OSV_QUERY_URL, data=json.dumps(query).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "hermes-locked-source-audit"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        if (not isinstance(result, dict) or set(result) - {"vulns", "next_page_token"}
                or not isinstance(result.get("vulns", []), list)):
            raise ValueError("invalid OSV response")
        for advisory in result.get("vulns", []):
            if not isinstance(advisory, dict) or not isinstance(advisory.get("id"), str):
                raise ValueError("OSV advisory has no ID")
            advisories.append(advisory)
        page = result.get("next_page_token", "")
        if not isinstance(page, str):
            raise ValueError("invalid OSV pagination token")
        if not page:
            return advisories
        if page in pages:
            raise ValueError("invalid OSV pagination token")
        pages.add(page)
        query = {**query, "page_token": page}


def audit_sources(packages: list[dict]) -> int:
    if not packages:
        return 0
    try:
        manifest = json.loads(SOURCE_REVIEWS.read_text())
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ValueError("unsupported source review schema")
        reviews = manifest["reviews"]
        if not isinstance(reviews, list):
            raise ValueError("reviews must be a list")
    except (OSError, ValueError, KeyError) as exc:
        print(f"Cannot read source reviews: {exc}", file=sys.stderr)
        return 2
    status = 0
    for package in packages:
        identity = source_identity(package)
        matching = [r for r in reviews if isinstance(r, dict) and r.get("package") == identity]
        label = f"{package['name']}=={package['version']}"
        if len(matching) != 1:
            print(f"{label}: unreviewed non-registry source: {identity}", file=sys.stderr)
            status = 2
            continue
        review = matching[0]
        try:
            print(f"{label}: auditing reviewed source {review['commit']}", flush=True)
            # A commit hit directly contradicts the source review and always fails.
            commit_findings = osv_advisories({"commit": review["commit"]})
            # Query ALL versions: fork/direct-wheel version labels do not establish
            # PyPI equivalence, and new advisories must receive a source-level review.
            package_findings = osv_advisories({"package": review["osv_package"]})
            accepted = review.get("reviewed_unaffected", {})
            unreviewed = [
                finding for finding in package_findings
                if accepted.get(finding["id"], {}).get("sha256") != advisory_digest(finding)
            ]
            for finding in commit_findings + unreviewed:
                print(f"{label}: source review required for {finding['id']}", file=sys.stderr)
            if commit_findings or unreviewed:
                status = 2
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            print(f"{label}: source audit incomplete: {exc}", file=sys.stderr)
            status = 2
    return status


def audit_lock(lockfile: Path) -> int:
    packages = tomllib.loads(lockfile.read_text())["package"]
    batches: list[dict[str, str]] = []
    source_packages: list[dict] = []
    for package in packages:
        if package.get("source", {}).get("registry") != "https://pypi.org/simple":
            if package.get("source") in ({"editable": "."}, {"virtual": "."}):
                continue  # The application or PM project itself.
            source_packages.append(package)
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
            # subprocess uses negative codes for signals; max(0, -9) would hide
            # a killed scanner and incorrectly report a clean audit.
            status = max(status, result.returncode if result.returncode >= 0 else 2)
    return max(status, audit_sources(source_packages))


def main(root: Path | None = None) -> int:
    if root is None:
        root = Path(__file__).resolve().parents[2]
    return max(audit_lock(root / lockfile) for lockfile in ("uv.lock", "pm/uv.lock"))


if __name__ == "__main__":
    raise SystemExit(main())

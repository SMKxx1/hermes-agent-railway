"""Railway distribution: resolve and pin the upstream base image for Dockerfile.railway.

The Railway image inherits the toolchain and service layout from ``nousresearch/hermes-agent``.
The weekly sync merges the image's source commit and pins its digest together. Dependencies and
frontend bundles are rebuilt from the fork's locks so security fixes reach the runtime.
Standard library only: this script runs in CI before any environment exists.

    python scripts/railway_base_image.py resolve [--tag main]   # prints JSON {digest, revision}
    python scripts/railway_base_image.py pin DIGEST [--refresh YYYY-MM-DD]
    python scripts/railway_base_image.py current                # prints the pinned digest
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

IMAGE = "nousresearch/hermes-agent"
DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.railway"
_INDEX_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _get(url: str, token: str, accept: str | None = None) -> tuple[bytes, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
        return resp.read(), dict(resp.headers)


def resolve(tag: str) -> dict:
    """Index digest of ``IMAGE:tag`` and the upstream commit its linux/amd64 image was built from."""
    token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{IMAGE}:pull"
    with urllib.request.urlopen(token_url, timeout=30) as resp:
        token = json.load(resp)["token"]
    registry = f"https://registry-1.docker.io/v2/{IMAGE}"
    body, headers = _get(f"{registry}/manifests/{tag}", token, _INDEX_TYPES)
    digest = {k.lower(): v for k, v in headers.items()}["docker-content-digest"]
    manifest = json.loads(body)
    if "manifests" in manifest:  # multi-arch index: Railway builds linux/amd64
        amd64 = next(m["digest"] for m in manifest["manifests"]
                     if m.get("platform", {}).get("architecture") == "amd64"
                     and m.get("platform", {}).get("os") == "linux")
        manifest = json.loads(_get(f"{registry}/manifests/{amd64}", token, _INDEX_TYPES)[0])
    config = json.loads(_get(f"{registry}/blobs/{manifest['config']['digest']}", token)[0])
    revision = (config.get("config", {}).get("Labels") or {}).get("org.opencontainers.image.revision", "")
    if not _DIGEST.fullmatch(digest) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SystemExit(f"unexpected registry answer: digest={digest!r} revision={revision!r}")
    return {"digest": digest, "revision": revision}


def current(text: str | None = None) -> str:
    text = DOCKERFILE.read_text(encoding="utf-8") if text is None else text
    match = re.search(rf"^ARG HERMES_BASE={re.escape(IMAGE)}@(sha256:[0-9a-f]{{64}})$", text, re.M)
    if match is None:
        raise SystemExit("Dockerfile.railway has no pinned ARG HERMES_BASE")
    return match.group(1)


def pin(digest: str, refresh: str | None = None) -> bool:
    """Point Dockerfile.railway (base ARG and base.digest label) at *digest*; True if changed."""
    if not _DIGEST.fullmatch(digest):
        raise SystemExit(f"not an image digest: {digest!r}")
    text = DOCKERFILE.read_text(encoding="utf-8")
    new = text.replace(current(text), digest)
    if refresh:
        new = re.sub(r"^ARG DEBIAN_SECURITY_REFRESH=.*$", f"ARG DEBIAN_SECURITY_REFRESH={refresh}", new, flags=re.M)
    if new != text:
        DOCKERFILE.write_text(new, encoding="utf-8")
    return new != text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("resolve").add_argument("--tag", default="main")
    pin_p = sub.add_parser("pin")
    pin_p.add_argument("digest")
    pin_p.add_argument("--refresh", help="also set ARG DEBIAN_SECURITY_REFRESH to this date")
    sub.add_parser("current")
    args = parser.parse_args(argv)
    if args.command == "resolve":
        print(json.dumps(resolve(args.tag)))
    elif args.command == "pin":
        print("changed" if pin(args.digest, args.refresh) else "unchanged")
    else:
        print(current())
    return 0


if __name__ == "__main__":
    sys.exit(main())

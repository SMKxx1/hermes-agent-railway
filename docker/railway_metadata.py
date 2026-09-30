"""Replace inherited upstream provenance with the Railway build's admitted revision."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from scripts.write_install_stamp import FALLBACK_COMMIT, write_stamp


def write_metadata(root: Path, marker: Path, revision: str, branch: str | None = None) -> dict:
    # An unstamped local build must never claim the base image's upstream commit.
    commit = FALLBACK_COMMIT if revision == "unversioned-source-build" else revision
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Railway build revision must be a full lowercase Git SHA")
    stamp = write_stamp(
        root / "install-stamp.json", commit=commit, branch=branch, dirty=False,
        source="docker", distribution="docker", update_mechanism="external",
    )
    stamp["pmRuntime"] = str(root / "pm-runtime")
    (root / "install-stamp.json").write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    (root / ".hermes_build_sha").write_text(revision + "\n", encoding="utf-8")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "schema": 1, "deployment_kind": "image", "manager": "docker",
        "image": "hermes-agent-railway", "version": stamp["displayVersion"],
        "revision": None if commit == FALLBACK_COMMIT else commit,
    }, sort_keys=True) + "\n", encoding="utf-8")
    marker.chmod(0o444)
    return stamp


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--branch")
    args = parser.parse_args()
    write_metadata(Path("/opt/hermes"), Path("/etc/hermes/image-provenance.json"),
                   args.revision, args.branch)

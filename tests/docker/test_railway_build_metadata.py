"""Railway overlays must identify their own code, never the inherited base image."""
import json

import pytest

from docker.railway_metadata import write_metadata
from hermes_cli import version_info


def test_railway_stamp_replaces_inherited_identity(tmp_path, monkeypatch):
    root = tmp_path / "image"
    root.mkdir()
    stamp_file = root / "install-stamp.json"
    stamp_file.write_text(json.dumps({"commit": "a" * 40, "baseVersion": "1.2.3"}))
    marker = tmp_path / "etc/hermes/image-provenance.json"
    revision = "b" * 40
    write_metadata(root, marker, revision, "main")
    monkeypatch.setattr(version_info, "_resolve_stamp_file", lambda: stamp_file)
    info = version_info._stamp_version_info()
    assert info.commit == revision
    assert info.display_version == info.derived_version == f"git.{revision[:7]}"
    assert info.distribution == "docker"
    stamp = json.loads(stamp_file.read_text())
    assert stamp["updateMechanism"] == "external"
    assert stamp["pmRuntime"] == str(root / "pm-runtime")
    assert json.loads(marker.read_text())["revision"] == revision
    assert (root / ".hermes_build_sha").read_text().strip() == revision
    assert marker.stat().st_mode & 0o777 == 0o444


def test_unversioned_or_invalid_build_cannot_claim_upstream_identity(tmp_path):
    marker = tmp_path / "provenance.json"
    write_metadata(tmp_path, marker, "unversioned-source-build")
    assert json.loads(marker.read_text())["revision"] is None
    before = (tmp_path / "install-stamp.json").read_bytes()
    with pytest.raises(ValueError, match="full lowercase Git SHA"):
        write_metadata(tmp_path, marker, "main")
    assert (tmp_path / "install-stamp.json").read_bytes() == before

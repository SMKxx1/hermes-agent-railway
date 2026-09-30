"""The real TUI session metadata must retain packaged build identity."""
from __future__ import annotations

import json

import pytest

from hermes_cli import version_info
from tui_gateway import server


@pytest.mark.parametrize("base, display, distance", [
    (None, "git.1234567", None),
    ("0.3.0", "0.3.0+4", 4),
])
def test_session_info_uses_packaged_display_version(tmp_path, monkeypatch, base, display, distance):
    stamp = tmp_path / "install-stamp.json"
    stamp.write_text(json.dumps({
        "baseVersion": base, "displayVersion": display, "distance": distance,
        "commit": "1234567" + "a" * 33, "source": "docker",
        "distribution": "docker", "updateMechanism": "external",
    }))
    monkeypatch.setattr(version_info, "_resolve_stamp_file", lambda: stamp)
    version_info._reset_version_info_cache()
    try:
        identity = version_info.get_version_info()
        info = server._session_info(None, {"cwd": str(tmp_path)})
        assert identity.display_version == display
        assert info["version"] == identity.display_version
    finally:
        version_info._reset_version_info_cache()

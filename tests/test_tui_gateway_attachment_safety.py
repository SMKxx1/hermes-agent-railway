"""Attachment staging must create fresh files without following existing links."""

import base64
import concurrent.futures
import threading
from datetime import datetime
from types import SimpleNamespace

from tui_gateway import server


def _stage(session, payload):
    return server._stage_session_file_attachment(
        session,
        raw_path="",
        data_url="data:text/plain;base64," + base64.b64encode(payload).decode("ascii"),
        name="report.txt",
    )


def test_file_attachment_skips_dangling_symlink(tmp_path):
    session = {"cwd": str(tmp_path), "profile_home": str(tmp_path / "home")}
    root = server._desktop_attachment_dir(session)
    outside = tmp_path / "outside.txt"
    link = root / "report.txt"
    link.symlink_to(outside)

    stored, uploaded = _stage(session, b"attachment bytes")

    assert uploaded
    assert stored.parent == root.resolve()
    assert stored != link
    assert stored.read_bytes() == b"attachment bytes"
    assert link.is_symlink()
    assert not outside.exists()


def test_concurrent_file_attachments_do_not_overwrite(tmp_path, monkeypatch):
    session = {"cwd": str(tmp_path), "profile_home": str(tmp_path / "home")}
    server._desktop_attachment_dir(session)
    original = server._unique_attachment_path
    barrier = threading.Barrier(2)
    selected = threading.local()

    def select_together(root, filename):
        target = original(root, filename)
        if not getattr(selected, "once", False):
            selected.once = True
            # Both uploads have selected the same unused filename. The write
            # itself must claim it exclusively, rather than trusting this check.
            barrier.wait(timeout=5)
        return target

    monkeypatch.setattr(server, "_unique_attachment_path", select_together)
    payloads = (b"first upload", b"second upload")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda payload: _stage(session, payload), payloads))

    assert results[0][0] != results[1][0]
    for (stored, uploaded), payload in zip(results, payloads):
        assert uploaded
        assert stored.read_bytes() == payload


def test_image_attachment_skips_dangling_symlink(tmp_path, monkeypatch):
    session = {"profile_home": str(tmp_path / "home")}
    root = server._session_images_dir(session)
    root.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    now = datetime(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr(server, "datetime", SimpleNamespace(now=lambda: now))
    link = root / f"upload_{now.strftime('%Y%m%d_%H%M%S')}_1.png"
    link.symlink_to(outside)

    stored = server._queue_attached_image(session, b"image bytes", ".png", prefix="upload")

    assert stored.parent == root
    assert stored != link
    assert stored.read_bytes() == b"image bytes"
    assert session["attached_images"] == [str(stored)]
    assert link.is_symlink()
    assert not outside.exists()

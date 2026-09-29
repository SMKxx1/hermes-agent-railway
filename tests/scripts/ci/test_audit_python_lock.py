"""Source reviews must not hide registry findings, new advisories or PM pins."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from scripts.ci import audit_python_lock as audit


def write_lock(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(
        f'[[package]]\nname = "{name}"\nversion = "{version}"\nsource = {source}\n'
        for name, version, source in entries
    ))


def test_unsupported_sources_fail_after_all_registry_versions_are_audited(tmp_path, monkeypatch, capsys):
    lockfile = tmp_path / "uv.lock"
    write_lock(lockfile, [
        ("project", "0.0.0", '{ editable = "." }'),
        ("wheel-only", "1.0", '{ url = "https://example.test/package.whl" }'),
        ("registry-package", "1.0", '{ registry = "https://pypi.org/simple" }'),
        ("fork", "1.0", '{ git = "https://example.test/fork.git#revision" }'),
        ("alternate-registry", "1.0", '{ registry = "https://example.test/simple" }'),
        ("registry-package", "2.0", '{ registry = "https://pypi.org/simple" }'),
    ])
    requirements = []

    def run(command, *, check):
        assert command[:5] == [audit.sys.executable, "-m", "pip_audit", "--disable-pip", "--no-deps"]
        requirements.append(Path(command[command.index("--requirement") + 1]).read_text())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.audit_lock(lockfile) == 2
    assert requirements == ["registry-package==1.0\n", "registry-package==2.0\n"]
    error = capsys.readouterr().err
    assert all(name in error for name in ("wheel-only==1.0", "fork==1.0", "alternate-registry==1.0"))


@pytest.mark.parametrize("returncode", [1, -9])
def test_pm_registry_findings_fail_the_combined_audit(tmp_path, monkeypatch, returncode):
    write_lock(tmp_path / "uv.lock", [
        ("application", "1.0", '{ registry = "https://pypi.org/simple" }'),
    ])
    write_lock(tmp_path / "pm/uv.lock", [
        ("pm-project", "0.0.0", '{ virtual = "." }'),
        ("runtime", "2.0", '{ registry = "https://pypi.org/simple" }'),
    ])
    requirements = []

    def run(command, *, check):
        pins = Path(command[command.index("--requirement") + 1]).read_text()
        requirements.append(pins)
        return SimpleNamespace(returncode=returncode if "runtime==" in pins else 0)

    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.main(tmp_path) == (returncode if returncode >= 0 else 2)
    assert requirements == ["application==1.0\n", "runtime==2.0\n"]


@contextmanager
def osv_server(respond):
    queries = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            query = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            queries.append(query)
            status, payload = respond(query)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", queries
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def source_review(tmp_path, monkeypatch):
    package = {
        "name": "reviewed-wheel", "version": "1.0",
        "source": {"url": "https://example.test/release.whl"},
        "wheels": [{"url": "https://example.test/release.whl", "hash": "sha256:" + "a" * 64}],
    }
    advisory = {"id": "REVIEWED-FIX", "modified": "2026-01-01T00:00:00Z", "affected": []}
    review = {
        "package": deepcopy(package), "commit": "b" * 40,
        "osv_package": {"ecosystem": "PyPI", "name": "reviewed-wheel"},
        "reviewed_unaffected": {advisory["id"]: {"sha256": audit.advisory_digest(advisory)}},
    }
    manifest = tmp_path / "reviews.json"
    manifest.write_text(json.dumps({"schema_version": 1, "reviews": [review]}))
    monkeypatch.setattr(audit, "SOURCE_REVIEWS", manifest)
    return package, advisory, review


@pytest.mark.parametrize("change", [
    "none", "source", "version", "wheel_hash", "new_advisory", "changed_advisory",
    "commit_finding", "malformed_response", "http_error",
])
def test_source_identity_and_advisory_review_cannot_silently_drift(tmp_path, monkeypatch, change):
    package, advisory, review = source_review(tmp_path, monkeypatch)
    if change == "source":
        package["source"]["url"] += "?replacement"
    elif change == "version":
        package["version"] = "2.0"
    elif change == "wheel_hash":
        package["wheels"][0]["hash"] = "sha256:" + "c" * 64

    def respond(query):
        if change == "http_error":
            return 503, {"error": "unavailable"}
        if change == "malformed_response":
            return 200, {"error": "not a vulnerability result"}
        if "commit" in query:
            return 200, {"vulns": [advisory]} if change == "commit_finding" else {}
        finding = dict(advisory)
        if change == "new_advisory":
            finding["id"] = "UNREVIEWED"
        elif change == "changed_advisory":
            finding["affected"] = [{"ranges": [{"events": [{"introduced": "0"}]}]}]
        return 200, {"vulns": [finding]}

    with osv_server(respond) as (url, queries):
        monkeypatch.setattr(audit, "OSV_QUERY_URL", url)
        assert audit.audit_sources([package]) == (0 if change == "none" else 2)
    if change in {"source", "version", "wheel_hash"}:
        assert queries == []
    else:
        assert queries[0] == {"commit": review["commit"]}
        if change not in {"malformed_response", "http_error"}:
            # No version can sneak into the package query and imply PyPI equivalence.
            assert queries[1] == {"package": review["osv_package"]}


@pytest.mark.parametrize("last_page", ["clean", "finding", "repeated_token", "invalid_token"])
def test_source_advisory_pagination_cannot_hide_a_later_finding(tmp_path, monkeypatch, last_page):
    package, advisory, _ = source_review(tmp_path, monkeypatch)

    def respond(query):
        if "commit" in query:
            return 200, {}
        if "page_token" not in query:
            return 200, {"vulns": [advisory], "next_page_token": "next-page"}
        if last_page == "repeated_token":
            return 200, {"next_page_token": "next-page"}
        if last_page == "invalid_token":
            return 200, {"next_page_token": False}
        return 200, {"vulns": [{"id": "NEW-FINDING"}]} if last_page == "finding" else {}

    with osv_server(respond) as (url, queries):
        monkeypatch.setattr(audit, "OSV_QUERY_URL", url)
        assert audit.audit_sources([package]) == (0 if last_page == "clean" else 2)
    assert len(queries) == 3
    assert queries[2] == {**queries[1], "page_token": "next-page"}

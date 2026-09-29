"""Unsupported sources must not hide registry findings or the PM runtime lock."""
from pathlib import Path
from types import SimpleNamespace

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
    assert "wheel-only==1.0" in error and "fork==1.0" in error


def test_pm_registry_findings_fail_the_combined_audit(tmp_path, monkeypatch):
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
        return SimpleNamespace(returncode=int("runtime==" in pins))

    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.main(tmp_path) == 1
    assert requirements == ["application==1.0\n", "runtime==2.0\n"]

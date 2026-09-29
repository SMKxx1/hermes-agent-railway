"""The fork's privileged guard enforces its allowlist without executing PR code."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"

# Execute the actual workflow shell and jq filters against a stateful local API
# double. No network credentials or GitHub writes are needed for these tests.
GH_DOUBLE = r'''
import json, os, subprocess, sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

args = sys.argv[1:]
assert args[0] == "api", args
args = args[1:]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
endpoint = next(arg for arg in args if arg.startswith("repos/"))
url = urlsplit(endpoint)
path = url.path.split("/actions/")[1]
state_path = Path(os.environ["GUARD_API_STATE"])
state = json.loads(state_path.read_text())
code = 0
if method == "PUT":
    workflow_id = int(path.split("/")[1])
    workflow = next(w for w in state["workflows"] if w["id"] == workflow_id)
    if "disable_error" in workflow:
        workflow["state"] = workflow["disable_error"]
        code = 1
    else:
        workflow["state"] = "disabled_manually"
elif method == "POST":
    run_id = int(path.split("/")[1])
    run = next(r for r in state["runs"] if r["id"] == run_id)
    if run.get("cancel_error"):
        run["status"] = run["cancel_error"]
        code = 1
    else:
        run["status"] = "completed"
        run["cancelled"] = True
else:
    if path == "workflows":
        key, records = "workflows", state["workflows"]
    elif path == "runs":
        status = parse_qs(url.query)["status"][0]
        key, records = "workflow_runs", [r for r in state["runs"] if r["status"] == status]
    elif path.startswith("workflows/"):
        workflow_id = int(path.split("/")[1])
        workflow = next(w for w in state["workflows"] if w["id"] == workflow_id)
        if workflow.get("get_error"):
            # Even a read that prints an inactive state must not hide its failure.
            code = 1
        key, records = None, [workflow]
    else:
        run_id = int(path.split("/")[1])
        key, records = None, [next(r for r in state["runs"] if r["id"] == run_id)]
    pages = [records[i:i + 100] for i in range(0, len(records), 100)]
    if "--paginate" not in args:
        pages = pages[:1]
    for page in pages:
        payload = {key: page} if key else page[0]
        subprocess.run(["jq", "-r", args[args.index("--jq") + 1]],
                       input=json.dumps(payload), text=True, check=True)
state_path.write_text(json.dumps(state))
sys.exit(code)
'''


def _workflow(name):
    return YAML(typ="safe").load((WORKFLOWS / name).read_text())


def _run_guard(tmp_path, state):
    workflow = _workflow("workflow-guard.yml")
    job = workflow["jobs"]["guard"]
    gh = tmp_path / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_DOUBLE)
    gh.chmod(0o755)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    result = subprocess.run(
        ["bash", "-c", job["steps"][0]["run"]],
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "KEEP": job["env"]["KEEP"],
            "GITHUB_REPOSITORY": "owner/fork",
            "GH_TOKEN": "local-test-only",
            "GUARD_API_STATE": str(state_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, json.loads(state_path.read_text())


@pytest.mark.platforms("linux", "macos")
def test_guard_handles_untrusted_prs_and_cancels_all_disallowed_runs(tmp_path):
    workflow = _workflow("workflow-guard.yml")
    triggers = workflow["on"]
    assert "pull_request_target" in triggers
    assert "paths" not in triggers["pull_request_target"]
    assert "workflow_call" in triggers
    assert _workflow("railway-ci.yml")["name"] in triggers["workflow_run"]["workflows"]
    assert {"requested", "completed"} <= set(triggers["workflow_run"]["types"])
    # The write token must never be paired with a checkout/action from PR code.
    assert all("uses" not in step for step in workflow["jobs"]["guard"]["steps"])
    assert "github.event." not in workflow["jobs"]["guard"]["steps"][0]["run"]
    sync_guard = _workflow("upstream-sync.yml")["jobs"]["guard"]
    assert sync_guard["uses"] == "./.github/workflows/workflow-guard.yml"
    assert sync_guard["permissions"]["actions"] == "write"

    keep = workflow["jobs"]["guard"]["env"]["KEEP"].split()
    paths = [*keep, ".github/workflows/new upstream.yml", keep[0] + ".upstream"]
    workflows = [{"id": i, "path": p, "state": "active"} for i, p in enumerate(paths)]
    workflows.append({"id": 99, "path": ".github/workflows/disabled.yml", "state": "disabled_manually"})
    statuses = ["queued", "in_progress", "waiting", "pending", "requested"]
    runs = [{"id": i, "path": p, "status": statuses[i % len(statuses)]} for i, p in enumerate(paths)]
    # A disabled workflow can still have queued runs, including beyond page 1.
    runs += [{"id": i + 100, "path": workflows[-1]["path"], "status": "queued"} for i in range(105)]
    result, state = _run_guard(tmp_path, {"workflows": workflows, "runs": runs})
    assert result.returncode == 0, result.stderr
    for item in state["workflows"]:
        assert item["state"] == ("active" if item["path"] in keep else "disabled_manually")
    for run in state["runs"]:
        assert run.get("cancelled", False) == (run["path"] not in keep)


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("status", ["completed", "in_progress"])
def test_guard_only_ignores_cancel_errors_when_the_run_already_finished(tmp_path, status):
    result, _ = _run_guard(tmp_path, {"workflows": [], "runs": [{
        "id": 1,
        "path": ".github/workflows/upstream.yml",
        "status": "queued",
        "cancel_error": status,
    }]})
    assert (result.returncode == 0) == (status == "completed"), result.stderr


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("state_after_error,get_error,accepted", [
    ("disabled_manually", False, True),
    ("disabled_inactivity", False, True),
    ("disabled_fork", False, True),
    ("deleted", False, True),
    ("active", False, False),
    ("unexpected", False, False),
    (None, False, False),
    ("disabled_manually", True, False),
])
def test_guard_only_ignores_disable_errors_after_confirming_inactive_state(
    tmp_path, state_after_error, get_error, accepted,
):
    path = ".github/workflows/upstream.yml"
    result, state = _run_guard(tmp_path, {"workflows": [{
        "id": 1, "path": path, "state": "active",
        "disable_error": state_after_error, "get_error": get_error,
    }], "runs": [{"id": 2, "path": path, "status": "queued"}]})
    assert (result.returncode == 0) is accepted, result.stderr
    # A benign race must still reach the sweep for runs queued before disable.
    assert state["runs"][0].get("cancelled", False) is accepted

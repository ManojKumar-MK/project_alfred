"""Local pipelines: strict step parsing and real runs in task worktrees."""

import json
import re
import sys
from pathlib import Path

import pytest
from conftest import wait_for
from fastapi.testclient import TestClient
from test_runs_api import create

from alfred_ui.pipelines import PipelineRunner, parse_steps

PYTHON = json.dumps(sys.executable)


def steps_toml(*steps: tuple[str, str, str]) -> str:
    """Render ``[[steps]]`` tables whose commands run a Python snippet."""
    tables = [
        f'[[steps]]\nrepository = "{repository}"\nname = "{name}"\n'
        f'command = [{PYTHON}, "-c", {json.dumps(code)}]\n'
        for repository, name, code in steps
    ]
    return "version = 1\n\n" + "\n".join(tables)


def run_state(client: TestClient, number: int) -> dict[str, object]:
    return client.get(f"/api/tasks/{number}/pipelines").json()["runs"][0]


STEP = 'version = 1\n[[steps]]\nrepository = "app"\nname = "t"\n'


def test_parse_rejects_shell_strings_unknown_keys_and_repositories() -> None:
    repositories = {"app"}
    ok = parse_steps(STEP + 'command = ["x"]\n', repositories)
    assert (ok[0].command, ok[0].timeout_minutes) == (("x",), 30)
    web = STEP.replace('"app"', '"web"')
    for text, message in (
        ("version = 2\n", "version = 1"),
        ("version = 1\nother = 1\n", "Unknown keys"),
        (STEP + 'command = "make test"\n', "array of strings"),
        (web + 'command = ["x"]\n', "configured repository"),
        (STEP + 'command = ["x"]\nshell = true\n', "Unknown keys in steps[1]: shell"),
        (STEP + 'command = ["x"]\ntimeout_minutes = 0\n', "1 to 720"),
        ("version = ", "Invalid TOML"),
    ):
        with pytest.raises(ValueError, match=re.escape(message)):
            parse_steps(text, repositories)


def test_config_endpoints_validate_before_saving(client: TestClient, workspace: Path) -> None:
    initial = client.get("/api/pipelines").json()
    assert initial["exists"] is False and initial["text"].startswith("version = 1")
    bad = client.put("/api/pipelines", json={"text": "version = 1\n[[steps]]\nname = 'x'\n"})
    assert bad.status_code == 400
    assert not (workspace.parent / "pipelines.toml").exists()
    text = steps_toml(("app", "Check", "print('ok')"))
    assert client.post("/api/pipelines/validate", json={"text": text}).json()["message"] == "1 step"
    assert (
        client.put("/api/pipelines", json={"text": text}).json()["message"]
        == "Saved 1 pipeline step"
    )
    assert client.get("/api/pipelines").json()["steps"][0]["name"] == "Check"


def test_run_passes_fails_skips_and_streams_logs(client: TestClient) -> None:
    create(client, 5, repos=["app", "library"])
    client.post("/api/tasks/5/worktrees", json={})
    client.put(
        "/api/pipelines",
        json={
            "text": steps_toml(
                ("app", "Build", "import os; print('built in', os.getcwd())"),
                ("app", "Test", "import sys; print('one failure'); sys.exit(3)"),
                ("app", "Package", "print('never runs')"),
                ("library", "Lint", "print('clean')"),
            )
        },
    )
    assert [step["name"] for step in client.get("/api/tasks/5/pipelines").json()["steps"]] == [
        "Build",
        "Test",
        "Package",
        "Lint",
    ]
    started = client.post("/api/tasks/5/pipelines", json={"actor": "mk"}).json()["run"]
    assert started["status"] == "running" and set(started["heads"]) == {"app", "library"}
    finished = wait_for(lambda: (run := run_state(client, 5))["status"] != "running" and run)
    assert finished["status"] == "failed"
    assert [(step["name"], step["status"], step["exit_code"]) for step in finished["steps"]] == [
        ("Build", "passed", 0),
        ("Test", "failed", 3),
        ("Package", "skipped", None),
        ("Lint", "passed", 0),
    ]
    log = client.get(f"/api/tasks/5/pipelines/{finished['id']}/steps/1/log").json()
    assert "built in" in log["text"] and "task-5/app" in log["text"]
    tail = client.get(
        f"/api/tasks/5/pipelines/{finished['id']}/steps/1/log", params={"offset": log["offset"]}
    ).json()
    assert tail["text"] == "" and tail["offset"] == log["offset"]
    escape = client.get("/api/tasks/5/pipelines/..%2F..%2Fstate/steps/1/log")
    assert escape.status_code in {400, 404}


def test_cancel_stops_the_running_step(client: TestClient) -> None:
    create(client, 6)
    client.post("/api/tasks/6/worktrees", json={})
    client.put(
        "/api/pipelines",
        json={
            "text": steps_toml(
                ("app", "Slow", "import time; time.sleep(60)"), ("app", "After", "print(1)")
            )
        },
    )
    run = client.post("/api/tasks/6/pipelines", json={}).json()["run"]
    wait_for(lambda: run_state(client, 6)["steps"][0]["status"] == "running")
    again = client.post("/api/tasks/6/pipelines", json={})
    assert "already has a running pipeline" in again.json()["error"]
    client.post(f"/api/tasks/6/pipelines/{run['id']}/cancel")
    finished = wait_for(lambda: (state := run_state(client, 6))["status"] != "running" and state)
    assert finished["status"] == "cancelled"
    assert [step["status"] for step in finished["steps"]] == ["cancelled", "cancelled"]
    stopped = client.post(f"/api/tasks/6/pipelines/{run['id']}/cancel")
    assert "is not running" in stopped.json()["error"]


def test_a_run_left_running_by_a_stopped_server_reads_as_interrupted(
    client: TestClient, workspace: Path
) -> None:
    create(client, 7)
    from alfred.config.loader import load_config

    config = load_config(workspace)
    folder = PipelineRunner.directory(config, 7) / "20260101T000000Z-abcdef"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps({"id": folder.name, "status": "running", "steps": []})
    )
    assert PipelineRunner().runs(config, 7)[0]["status"] == "interrupted"
    no_worktrees = client.post("/api/tasks/7/pipelines", json={})
    assert "has no worktrees" in no_worktrees.json()["error"]

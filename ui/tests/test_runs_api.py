"""Run, worktree, and session endpoints against real Git, real tmux, and a scripted agent."""

import json
import subprocess
from pathlib import Path

from conftest import wait_for
from fastapi.testclient import TestClient


def runs(workspace: Path, number: int) -> list[dict[str, object]]:
    document = json.loads((workspace.parent / "state" / "runs.json").read_text())
    return [item for item in document["runs"] if item["task_number"] == number]


def task(workspace: Path, number: int) -> dict[str, object]:
    document = json.loads((workspace.parent / "state" / "tasks.json").read_text())
    return next(item for item in document["tasks"] if item["task_number"] == number)


def create(
    client: TestClient, number: int, description: str = "Scripted", **fields: object
) -> None:
    body = {
        "task": number,
        "title": f"Task {number}",
        "description": description,
        "branch": f"feature/task-{number}",
        "repos": ["app"],
        "assign": "fake",
        **fields,
    }
    assert client.post("/api/tasks", json=body).status_code == 200


def session_exists(name: str) -> bool:
    return (
        subprocess.run(["tmux", "has-session", "-t", f"={name}"], capture_output=True).returncode
        == 0
    )


def test_direct_run_completes_and_worktree_is_committed_and_pushed(
    client: TestClient, workspace: Path, prefix: str
) -> None:
    create(client, 1)
    triggered = client.post("/api/runs/trigger", json={"tasks": [1]})
    assert triggered.json()["message"].startswith("Task 1: running")
    assert session_exists(f"{prefix}-1-fake")
    wait_for(lambda: runs(workspace, 1)[-1]["run_status"] == "completed")
    assert task(workspace, 1)["status"] == "MR in Review"

    detail = client.get("/api/tasks/1").json()
    assert detail["task"]["derived"]["plan"]["required"] is False
    assert {item["event_type"] for item in detail["events"]} >= {
        "RUN_STARTED",
        "PROGRESS",
        "RUN_COMPLETED",
    }
    assert detail["prompts"][0]["phase"] == "execution"
    assert detail["completions"]["pending"][0]["report"]["status"] == "success"
    assert detail["knowledge"][0]["agent"] == "fake"

    worktrees = client.get("/api/tasks/1/worktrees", params={"diff": True}).json()["worktrees"]
    assert worktrees[0]["changes"][0] == {
        "code": "??",
        "path": "fake-agent-task-1.txt",
        "label": "untracked",
    }
    assert worktrees[0]["untracked"][0]["content"].startswith("Change for task 1")
    committed = client.post(
        "/api/tasks/1/worktrees/commit", json={"type": "feature", "message": "Add it"}
    )
    assert committed.json()["commits"][0]["message"] == "[FEATURE] Add it"
    nothing = client.post("/api/tasks/1/worktrees/commit", json={"type": "fix", "message": "Again"})
    assert nothing.json()["message"] == "Nothing to commit"
    bad_type = client.post("/api/tasks/1/worktrees/commit", json={"type": "chore", "message": "x"})
    assert "Unknown commit type" in bad_type.json()["error"]
    pushed = client.post("/api/tasks/1/worktrees/push", json={"repo": "app"})
    assert pushed.json()["pushed"] == [
        {"repository": "app", "branch": "feature/task-1", "remote": "origin", "review_url": ""}
    ]
    after = client.get("/api/tasks/1/worktrees").json()["worktrees"][0]
    assert after["remote"]["exists"] is True
    assert after["remote"]["unpushed"] == 0
    assert after["commits"][0]["subject"] == "[FEATURE] Add it"
    assert after["ahead"] == 1

    # A finished run's session stays alive; it can be captured, messaged, and closed.
    name = f"{prefix}-1-fake"
    captured = client.post(f"/api/sessions/{name}/capture", json={"reason": "manual"}).json()
    assert captured["transcript"]["reason"] == "manual"
    transcripts = client.get("/api/transcripts", params={"task": 1}).json()["transcripts"]
    reasons = {item["reason"] for item in transcripts}
    assert {"manual", "completed"} <= reasons
    content = client.get(
        "/api/transcripts/read", params={"path": transcripts[0]["relative"]}
    ).json()
    assert "# Alfred session transcript" in content["content"]
    sent = client.post(f"/api/sessions/{name}/send", json={"text": "hello there"})
    assert sent.json()["message"] == f"Sent message to {name}"
    wait_for(
        lambda: (
            "Operator: hello there" in client.get(f"/api/sessions/{name}/capture").json()["content"]
        )
    )
    keys = client.post(f"/api/sessions/{name}/keys", json={"keys": ["Enter"]})
    assert keys.status_code == 200
    unsafe = client.post(f"/api/sessions/{name}/keys", json={"keys": ["; rm -rf"]})
    assert unsafe.status_code == 400
    closed = client.post(f"/api/sessions/{name}/kill", json={})
    assert closed.json()["message"] == f"Closed session {name}"
    assert not session_exists(name)


def test_plan_execution_continue(client: TestClient, workspace: Path, prefix: str) -> None:
    create(client, 2, mode="plan-execution")
    client.post("/api/runs/trigger", json={"tasks": [2]})
    wait_for(lambda: task(workspace, 2)["planning_state"] == "started")
    detail = wait_for(
        lambda: (
            (value := client.get("/api/tasks/2").json())["task"]["derived"]["plan"]["reported"]
            and value
        )
    )
    assert detail["task"]["derived"]["actions"]["continue"]["enabled"] is True
    assert not (workspace.parent / "worktrees" / "task-2").exists()
    again = client.post("/api/runs/trigger", json={"tasks": [2]})
    assert "already has an active run" in again.json()["error"]
    continued = client.post("/api/tasks/2/run/continue", json={"note": "Ship it"})
    assert continued.json()["message"] == "Execution phase started for task 2"
    assert continued.json()["transcript"]["reason"] == "plan"
    wait_for(lambda: runs(workspace, 2)[-1]["run_status"] == "completed")
    assert len(runs(workspace, 2)) == 1
    assert task(workspace, 2)["planning_state"] == "completed"
    second = client.post("/api/tasks/2/run/continue", json={})
    assert "no plan awaiting approval" in second.json()["error"]


def test_events_complete_stop_and_reopen(client: TestClient, workspace: Path, prefix: str) -> None:
    create(client, 3, description="Waits. [fake:wait]", worktree="disabled", branch="")
    client.post("/api/runs/trigger", json={"tasks": [3]})
    wrong = client.post(
        "/api/tasks/3/run/event",
        json={"type": "progress", "actor": "agent:ghost", "override": False},
    )
    assert wrong.status_code == 403
    progress = client.post("/api/tasks/3/run/event", json={"type": "coding", "note": "Working"})
    assert progress.json()["message"] == "Event coding recorded for task 3"
    blocked = client.post(
        "/api/tasks/3/run/complete",
        json={"result": "blocked", "note": "Need data", "actor": "agent:fake", "override": False},
    )
    assert blocked.json()["message"] == "Task 3 completion reported as blocked"
    assert runs(workspace, 3)[-1]["run_status"] == "blocked"
    resumed = client.post("/api/tasks/3/run/event", json={"type": "unblocked", "note": "Data here"})
    assert resumed.status_code == 200
    assert runs(workspace, 3)[-1]["run_status"] == "running"

    stopped = client.post("/api/tasks/3/run/stop", json={"reason": "Enough"})
    assert stopped.json()["transcript"]["reason"] == "stopped"
    assert not session_exists(f"{prefix}-3-fake")
    assert task(workspace, 3)["status"] == "Pending"
    no_run = client.post("/api/tasks/3/run/stop", json={})
    assert no_run.json()["error"] == "Task 3 has no active run"

    reopened = client.post("/api/tasks/3/run/reopen", json={})
    assert reopened.json()["session_name"] == f"{prefix}-3-fake"
    assert session_exists(f"{prefix}-3-fake")
    busy = client.post(f"/api/sessions/{prefix}-3-fake/kill", json={})
    assert "stop the run instead" in busy.json()["error"]
    reassigned = client.post(
        "/api/tasks/3/reassign", json={"agent": "recorder", "mode": "stop-and-switch"}
    )
    assert reassigned.json()["stopped"] is True
    assert task(workspace, 3)["assigned_agent_alias"] == "recorder"


def test_trigger_all_parallel_and_queue_without_tmux(
    client: TestClient, workspace: Path, monkeypatch: object
) -> None:
    for number in (4, 5, 6):
        create(client, number, description="Waits. [fake:wait]", dispatch="queued")
    empty = client.post("/api/runs/trigger", json={"tasks": []})
    assert "Choose tasks to trigger" in empty.json()["error"]
    partial = client.post("/api/runs/trigger", json={"all": True, "parallel": 2})
    assert partial.json()["skipped"] == [6]
    assert [item["task_number"] for item in partial.json()["runs"]] == [4, 5]
    zero = client.post("/api/runs/trigger", json={"tasks": [6], "parallel": 0})
    assert "parallel must be at least 1" in zero.json()["error"]

    import os

    original = os.environ["PATH"]
    os.environ["PATH"] = "/usr/bin:/bin"
    try:
        queued = client.post("/api/runs/trigger", json={"tasks": [6]})
    finally:
        os.environ["PATH"] = original
    assert queued.json()["message"] == "Task 6: queued"
    snapshot = client.get("/api/snapshot").json()
    assert snapshot["queue"] == [6]
    derived = next(item for item in snapshot["tasks"] if item["task_number"] == 6)["derived"]
    assert derived["queued"] is True
    assert derived["actions"]["trigger"]["enabled"] is True
    redispatched = client.post("/api/runs/trigger", json={"tasks": [6]})
    assert redispatched.json()["message"].startswith("Task 6: running")
    assert [run["run_status"] for run in runs(workspace, 6)] == ["stopped", "running"]


def test_worktree_create_and_remove(client: TestClient, workspace: Path) -> None:
    create(client, 7, repos=["app", "library"])
    created = client.post("/api/tasks/7/worktrees", json={"repos": []})
    assert set(created.json()["worktrees"]) == {"app", "library"}
    root = workspace.parent / "worktrees" / "task-7"
    (root / "app" / "scratch.txt").write_text("dirty", encoding="utf-8")
    refused = client.post("/api/tasks/7/worktrees/remove", json={})
    assert "uncommitted changes: app" in refused.json()["error"]
    removed = client.post("/api/tasks/7/worktrees/remove", json={"force": True})
    assert removed.json()["message"] == "Removed worktrees for task 7: app, library"
    assert not root.exists()
    nothing = client.post("/api/tasks/7/worktrees/remove", json={})
    assert nothing.json()["error"] == "Task 7 has no worktrees"


def test_foreign_and_invalid_sessions_are_refused(client: TestClient) -> None:
    foreign = client.post("/api/sessions/someone-else/send", json={"text": "hi"})
    assert foreign.status_code == 403
    invalid = client.get("/api/sessions/bad name/capture")
    assert invalid.status_code in {400, 403}


def test_cancel_stops_the_live_run_and_removes_worktrees(
    client: TestClient, workspace: Path, prefix: str
) -> None:
    create(client, 9, description="Waits. [fake:wait]")
    client.post("/api/runs/trigger", json={"tasks": [9]})
    assert session_exists(f"{prefix}-9-fake")
    root = workspace.parent / "worktrees" / "task-9"
    assert root.exists()
    cancelled = client.post(
        "/api/tasks/9/cancel", json={"reason": "Superseded", "cleanup": True, "force": True}
    )
    assert cancelled.json()["message"] == "Task 9: Cancelled"
    assert cancelled.json()["transcript"]["reason"] == "cancelled"
    assert not session_exists(f"{prefix}-9-fake")
    assert not root.exists()
    assert runs(workspace, 9)[-1]["run_status"] == "stopped"
    assert task(workspace, 9)["lifecycle_phase"] == "archived"


def test_branch_edit_renames_the_worktree_branch(client: TestClient, workspace: Path) -> None:
    create(client, 10)
    client.post("/api/tasks/10/worktrees", json={"repos": []})
    updated = client.patch(
        "/api/tasks/10", json={"branch": "feature/renamed-10", "title": "Renamed"}
    )
    assert updated.status_code == 200, updated.text
    record = task(workspace, 10)
    assert (record["branch_name"], record["title"]) == ("feature/renamed-10", "Renamed")
    worktree = workspace.parent / "worktrees" / "task-10" / "app"
    current = subprocess.run(
        ["git", "-C", str(worktree), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert current.stdout.strip() == "feature/renamed-10"
    invalid = client.patch("/api/tasks/10", json={"branch": "bad..name"})
    assert invalid.status_code == 400

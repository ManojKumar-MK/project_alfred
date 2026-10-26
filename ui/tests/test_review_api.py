"""Pull-request style review against real Git, real tmux, and the scripted agent."""

import json
import subprocess
from pathlib import Path

import pytest
from conftest import wait_for
from fastapi.testclient import TestClient
from test_runs_api import create, runs, task

from alfred_ui.review import ReviewStore, feedback_message


def finished_task(client: TestClient, workspace: Path, number: int) -> None:
    create(client, number)
    client.post("/api/runs/trigger", json={"tasks": [number]})
    wait_for(lambda: runs(workspace, number)[-1]["run_status"] == "completed")
    client.post(
        f"/api/tasks/{number}/worktrees/commit", json={"type": "feature", "message": "Add it"}
    )


def test_comments_request_changes_then_approve_and_merge(
    client: TestClient, workspace: Path
) -> None:
    finished_task(client, workspace, 3)
    path = "fake-agent-task-3.txt"
    added = client.post(
        "/api/tasks/3/review/comments",
        json={"repository": "app", "path": path, "line": 1, "body": "Say why", "actor": "mk"},
    ).json()["comment"]
    whole_file = client.post(
        "/api/tasks/3/review/comments",
        json={"repository": "app", "path": path, "body": "Rename this file"},
    ).json()["comment"]
    edited = client.post(
        f"/api/tasks/3/review/comments/{added['id']}", json={"body": "Say why it changed"}
    ).json()["comment"]
    assert (edited["body"], edited["author"], edited["line"]) == ("Say why it changed", "mk", 1)
    client.post(f"/api/tasks/3/review/comments/{whole_file['id']}/delete")

    requested = client.post(
        "/api/tasks/3/review/request-changes", json={"note": "Nearly there"}
    ).json()
    assert requested["message"].startswith("Changes requested on task 3: 1 comment reopened")
    assert task(workspace, 3)["notes"].startswith("Review of task 3: changes requested.")
    assert f"app/{path}:1\n   Say why it changed" in task(workspace, 3)["notes"]
    document = client.get("/api/tasks/3/review").json()
    assert document["verdict"]["decision"] == "changes_requested"
    assert [item["sent_at"] is not None for item in document["comments"]] == [True]
    # The reopened run reuses the session and finishes again.
    wait_for(
        lambda: len(runs(workspace, 3)) == 2 and runs(workspace, 3)[-1]["run_status"] == "completed"
    )

    again = client.post("/api/tasks/3/review/request-changes", json={})
    assert "Add a comment or a summary" in again.json()["error"]

    client.post(f"/api/tasks/3/review/comments/{added['id']}", json={"resolved": True})
    approved = client.post("/api/tasks/3/review/approve", json={}).json()
    assert approved["message"] == "Task 3 approved"
    assert task(workspace, 3)["status"] == "MR in Review"
    merged = client.post("/api/tasks/3/merge", json={"mr": "https://git.example.invalid/mr/1"})
    assert merged.status_code == 200


def test_comment_validation_and_unknown_ids(client: TestClient, workspace: Path) -> None:
    create(client, 4)
    blank = client.post(
        "/api/tasks/4/review/comments", json={"repository": "app", "path": "a.txt", "body": "  "}
    )
    assert "cannot be empty" in blank.json()["error"]
    line_zero = client.post(
        "/api/tasks/4/review/comments",
        json={"repository": "app", "path": "a.txt", "line": 0, "body": "x"},
    )
    assert line_zero.status_code == 400
    missing = client.post("/api/tasks/4/review/comments/nope", json={"resolved": True})
    assert missing.json() == {"error": "Review comment nope not found", "kind": "key"}
    assert client.get("/api/tasks/99/review").status_code == 400


def test_store_keeps_links_once_and_rejects_unknown_versions(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path)
    link = {"repository": "app", "branch": "b", "url": "https://git.example.invalid/mr/1"}
    store.add_links(5, [link])
    store.add_links(5, [link])
    assert [item["url"] for item in store.load(5)["links"]] == [link["url"]]
    store.path(6).write_text(json.dumps({"version": 99, "task_number": 6}))
    with pytest.raises(ValueError, match="Unsupported review document"):
        store.load(6)


def test_feedback_message_lists_locations_and_removed_lines() -> None:
    comments = [
        {"repository": "app", "path": "a.py", "line": 4, "side": "old", "body": "Keep\nthis"},
        {"repository": "app", "path": "b.py", "line": None, "side": "new", "body": "Split"},
    ]
    assert feedback_message(2, comments, "") == (
        "Review of task 2: changes requested.\n\n"
        "Address each comment, commit, then report progress:\n"
        "1. app/a.py:4 (removed line)\n   Keep\n   this\n"
        "2. app/b.py\n   Split"
    )


def test_branch_is_compared_with_the_remote_default_branch(
    client: TestClient, workspace: Path, tmp_path: Path
) -> None:
    root = workspace.parent.parent
    app = root / "app"
    git = ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "push", "-q", "origin", "main"], cwd=app, check=True)
    # Someone else merges work upstream; the local main never sees it, only the fetched ref.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(root / "app-remote.git"), str(other)], check=True)
    (other / "upstream.txt").write_text("merged elsewhere\n")
    subprocess.run([*git, "add", "upstream.txt"], cwd=other, check=True)
    subprocess.run([*git, "commit", "-qm", "Upstream"], cwd=other, check=True)
    subprocess.run([*git, "push", "-q", "origin", "main"], cwd=other, check=True)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=app, check=True)

    create(client, 8)
    client.post("/api/tasks/8/worktrees", json={})
    tree = root / ".alfred" / "worktrees" / "task-8" / "app"
    subprocess.run(["git", "merge", "-q", "origin/main"], cwd=tree, check=True)
    (tree / "mine.txt").write_text("task work\n")
    client.post("/api/tasks/8/worktrees/commit", json={"type": "feature", "message": "Mine"})

    detail = client.get("/api/tasks/8/worktrees", params={"diff": True}).json()["worktrees"][0]
    assert detail["base"] == "origin/main"
    assert [commit["subject"] for commit in detail["commits"]] == ["[FEATURE] Mine"]
    assert "upstream.txt" not in detail["committed_diff"]["text"]

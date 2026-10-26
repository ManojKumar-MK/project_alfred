"""Read-only Git details for task worktrees: changes, diffs, commits, and push state.

Nothing here writes to a repository or contacts a remote; push state is read from the
remote-tracking ref that ``alfred worktree push`` updates locally.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from alfred.adapters.git.worktrees import GitWorktreeManager, WorktreeStatus
from alfred.adapters.process import SubprocessRunner
from alfred.config.models import AlfredConfig
from alfred.ports.process import ProcessResult, ProcessRunner

MAX_DIFF_CHARACTERS = 400_000
MAX_UNTRACKED_PREVIEWS = 20
MAX_PREVIEW_BYTES = 64_000
LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%aI%x1f%s"


def summaries(config: AlfredConfig, task_numbers: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Return a light dirty/branch summary for each task that has worktrees."""
    manager = GitWorktreeManager(
        config.workspace, config.runtime.worktree_directory, SubprocessRunner()
    )
    result: dict[int, list[dict[str, Any]]] = {}
    for number in task_numbers:
        try:
            statuses = manager.statuses(number)
        except (OSError, RuntimeError, ValueError):
            continue
        if statuses:
            result[number] = [_summary(status) for status in statuses]
    return result


def tasks_with_worktrees(config: AlfredConfig) -> list[int]:
    """Return task numbers that have a worktree directory."""
    root = config.runtime.worktree_directory
    if not root.is_dir():
        return []
    numbers: list[int] = []
    for path in root.iterdir():
        suffix = path.name[5:]
        if path.is_dir() and path.name.startswith("task-") and suffix.isdigit():
            numbers.append(int(suffix))
    return sorted(numbers)


def worktree_details(
    config: AlfredConfig,
    statuses: tuple[WorktreeStatus, ...],
    *,
    include_diff: bool,
    runner: ProcessRunner | None = None,
) -> list[dict[str, Any]]:
    """Describe each worktree with changes, commits, and optionally full diffs."""
    process = runner or SubprocessRunner()
    return [_details(process, config, status, include_diff) for status in statuses]


def _summary(status: WorktreeStatus) -> dict[str, Any]:
    changes = [line for line in status.changes.splitlines() if line.strip()]
    return {
        "repository": status.repository,
        "path": str(status.path),
        "branch": status.branch,
        "dirty": bool(changes),
        "changes": len(changes),
    }


def _details(
    runner: ProcessRunner,
    config: AlfredConfig,
    status: WorktreeStatus,
    include_diff: bool,
) -> dict[str, Any]:
    path = status.path

    def git(*arguments: str) -> ProcessResult:
        return runner.run(("git", "-c", "core.quotepath=off", *arguments), cwd=path, check=False)

    repository = next(
        (item for item in config.workspace.repositories if item.name == status.repository), None
    )
    remote = repository.remote if repository else ""
    base = repository.default_branch if repository else ""
    # Compare with the remote's default branch when it is known locally, as a merge request
    # would; a stale local default branch would otherwise count other people's merged work.
    if base and remote:
        tracking = f"{remote}/{base}"
        if git("rev-parse", "--verify", "--quiet", f"refs/remotes/{tracking}").returncode == 0:
            base = tracking
    changes = _porcelain(git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout)
    head = git("rev-parse", "--short", "HEAD").stdout.strip()
    subject = git("log", "-1", "--format=%s").stdout.strip()
    ahead, behind = _counts(git, f"{base}...HEAD") if base else (None, None)
    commits = (
        _commits(git("log", "--max-count=50", f"--format={LOG_FORMAT}", f"{base}..HEAD").stdout)
        if base
        else []
    )
    remote_state: dict[str, Any] = {"remote": remote, "ref": "", "exists": False}
    if remote and status.branch:
        ref = f"refs/remotes/{remote}/{status.branch}"
        exists = git("rev-parse", "--verify", "--quiet", ref).returncode == 0
        remote_ahead, remote_behind = _counts(git, f"{ref}...HEAD") if exists else (None, None)
        remote_state = {
            "remote": remote,
            "ref": ref,
            "exists": exists,
            "unpushed": remote_ahead,
            "behind": remote_behind,
        }
    details: dict[str, Any] = {
        "repository": status.repository,
        "path": str(path),
        "branch": status.branch,
        "head": head,
        "head_subject": subject,
        "base": base,
        "ahead": ahead,
        "behind": behind,
        "commits": commits,
        "changes": changes,
        "dirty": bool(changes),
        "remote": remote_state,
    }
    if include_diff:
        details["diff"] = _capped(git("diff", "HEAD", "--no-color", "--no-ext-diff").stdout)
        details["diff_stat"] = git("diff", "HEAD", "--stat", "--no-color").stdout.rstrip()
        if base:
            committed = git("diff", f"{base}...HEAD", "--no-color", "--no-ext-diff").stdout
            details["committed_diff"] = _capped(committed)
            details["committed_stat"] = git(
                "diff", f"{base}...HEAD", "--stat", "--no-color"
            ).stdout.rstrip()
        details["untracked"] = _untracked_previews(path, changes)
    return details


def _counts(git: Callable[..., ProcessResult], revisions: str) -> tuple[int | None, int | None]:
    """Return (right-only, left-only) commit counts for ``left...right``."""
    result = git("rev-list", "--left-right", "--count", revisions)
    values = result.stdout.split()
    if result.returncode != 0 or len(values) != 2:
        return None, None
    left, right = (int(value) for value in values)
    return right, left


def _porcelain(output: str) -> list[dict[str, str]]:
    """Parse ``git status --porcelain=v1 -z`` entries, including renames."""
    entries: list[dict[str, str]] = []
    parts = output.split("\0")
    index = 0
    while index < len(parts):
        item = parts[index]
        index += 1
        if len(item) < 4:
            continue
        code, file_path = item[:2], item[3:]
        entry = {"code": code, "path": file_path, "label": _label(code)}
        if "R" in code or "C" in code:
            entry["original"] = parts[index] if index < len(parts) else ""
            index += 1
        entries.append(entry)
    return entries


def _label(code: str) -> str:
    if code == "??":
        return "untracked"
    if "U" in code or code in {"AA", "DD"}:
        return "conflict"
    for letter, label in (
        ("R", "renamed"),
        ("C", "copied"),
        ("A", "added"),
        ("D", "deleted"),
        ("M", "modified"),
        ("T", "type changed"),
    ):
        if letter in code:
            return label
    return "changed"


def _commits(output: str) -> list[dict[str, str]]:
    commits: list[dict[str, str]] = []
    for line in output.splitlines():
        values = line.split("\x1f")
        if len(values) == 5:
            full, short, author, date, subject = values
            commits.append(
                {"hash": full, "short": short, "author": author, "date": date, "subject": subject}
            )
    return commits


def _capped(text: str) -> dict[str, Any]:
    if len(text) <= MAX_DIFF_CHARACTERS:
        return {"text": text, "truncated": False}
    return {"text": text[:MAX_DIFF_CHARACTERS], "truncated": True}


def _untracked_previews(root: Path, changes: list[dict[str, str]]) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for change in changes:
        if change["code"] != "??":
            continue
        if len(previews) >= MAX_UNTRACKED_PREVIEWS:
            break
        path = (root / change["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            continue
        with path.open("rb") as handle:
            data = handle.read(MAX_PREVIEW_BYTES + 1)
        binary = b"\0" in data
        previews.append(
            {
                "path": change["path"],
                "size": path.stat().st_size,
                "binary": binary,
                "truncated": len(data) > MAX_PREVIEW_BYTES,
                "content": ""
                if binary
                else data[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace"),
            }
        )
    return previews

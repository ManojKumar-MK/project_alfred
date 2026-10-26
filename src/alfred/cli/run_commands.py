"""Run and Git worktree command handlers."""

import argparse
import sys

from alfred.bootstrap import AlfredServices
from alfred.domain.constants import RunStatus, TaskStatus
from alfred.domain.models import MergeRequestOptions


def handle_run(args: argparse.Namespace, services: AlfredServices) -> int:
    """Execute one run lifecycle command."""
    if args.action == "trigger":
        if args.all:
            task_numbers = [
                task.task_number
                for task in services.tasks.list()
                if task.status == TaskStatus.QUEUED
            ]
        else:
            task_numbers = _integer_csv(args.tasks)
        if not task_numbers:
            raise ValueError("Provide --tasks or queue at least one task for --all")
        runs = services.runs.trigger(
            task_numbers,
            parallel=args.parallel,
            actor=args.actor,
        )
        for run in runs:
            print(f"Task {run.task_number}: {run.run_status} ({run.run_id})")
        skipped = list(dict.fromkeys(task_numbers))[args.parallel :]
        if skipped:
            print(f"Not dispatched (--parallel {args.parallel}): {', '.join(map(str, skipped))}")
        return 0

    if args.action == "list":
        runs = services.runs.list()
        if args.status:
            selected_status = RunStatus(args.status)
            runs = tuple(run for run in runs if run.run_status == selected_status)
        if not runs:
            print("No runs")
        for run in runs:
            print(
                f"{run.run_id} | task={run.task_number} | {run.agent_alias} | "
                f"{run.run_status} | {run.started_at} | session={run.session_name or '-'}"
            )
        return 0

    if args.action == "stop":
        run = services.runs.stop(
            args.task,
            args.reason,
            cleanup=cleanup_requested(args.cleanup),
            force=args.force,
        )
        print(f"Stopped run {run.run_id} for task {args.task}")
        return 0

    if args.action == "continue":
        run = services.runs.continue_execution(
            args.task,
            args.note or "Plan approved.",
            actor=args.actor,
        )
        print(f"Execution phase started for task {run.task_number}")
        return 0

    if args.action == "attach":
        active_run = services.runs.active(args.task)
        if active_run is None or not active_run.session_name:
            print(f"No active session for task {args.task}")
        else:
            print(f"tmux attach-session -t {active_run.session_name}")
        return 0

    if args.action == "sessions":
        names = services.runs.session_names()
        if args.task is not None:
            active = services.runs.active(args.task)
            names = (
                (active.session_name,)
                if active is not None and active.session_name in names
                else ()
            )
        if not names:
            print("No active Alfred sessions")
        for name in names:
            print(name)
        return 0

    if args.action == "event":
        task = services.runs.record_event(
            args.task,
            args.type,
            args.note,
            actor=args.actor,
            override_actor=args.override_manager,
        )
        print(f"Event {args.type} recorded for task {task.task_number}")
        return 0

    if args.action == "complete":
        result = args.status_alias or args.result
        if result is None:
            raise ValueError("--result or --status is required")
        summary = args.summary_alias or args.note
        report = services.runs.complete(
            args.task,
            result,
            summary,
            actor=args.actor,
            override_actor=args.override_manager,
        )
        print(f"Task {report.task_number} completion reported as {report.status}")
        return 0

    if args.action == "reopen":
        run = services.runs.reopen(args.task, actor=args.actor)
        print(f"Task {run.task_number} session reopened")
        if run.session_name:
            print(f"tmux attach-session -t {run.session_name}")
        return 0
    raise ValueError(f"Unsupported run action: {args.action}")


def handle_worktree(args: argparse.Namespace, services: AlfredServices) -> int:
    """Execute one task worktree command."""
    if args.action == "create":
        task = services.tasks.require(args.task)
        created = services.worktrees.create(task, _csv(args.repos))
        for repository, path in created.items():
            print(f"{repository}: {path}")
        return 0
    if args.action == "status":
        statuses = services.worktrees.statuses(args.task)
        if not statuses:
            print(f"No worktrees for task {args.task}")
        for status in statuses:
            changes = status.changes or "clean"
            print(f"{status.repository} [{status.branch}]: {changes}")
        return 0
    if args.action == "commit":
        results = services.commits.commit(
            args.task,
            args.type,
            args.message,
            repository=args.repo,
        )
        if not results:
            print("Nothing to commit")
        for result in results:
            print(f"{result.repository}: {result.revision} {result.message}")
        return 0
    if args.action == "push":
        merge_request = None
        if args.merge_request:
            title = args.title.strip() or services.tasks.require(args.task).title
            merge_request = MergeRequestOptions(title=title, description=args.description)
        push_results = services.commits.push(
            args.task, repository=args.repo, merge_request=merge_request
        )
        for push_result in push_results:
            print(f"{push_result.repository}: pushed {push_result.branch} to {push_result.remote}")
            if push_result.review_url:
                print(f"{push_result.repository}: review {push_result.review_url}")
        return 0
    raise ValueError(f"Unsupported worktree action: {args.action}")


def cleanup_requested(value: str) -> bool:
    """Resolve a --cleanup choice, prompting only on an interactive terminal."""
    if value == "yes":
        return True
    if value == "no" or not sys.stdin.isatty():
        return False
    return input("Cleanup task worktrees as well? [y/N]: ").strip().lower() in {"y", "yes"}


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _integer_csv(value: str) -> list[int]:
    try:
        return [int(item) for item in _csv(value)]
    except ValueError as exc:
        raise ValueError("Tasks must be comma-separated task numbers") from exc

"""Run and worktree command handler tests."""

import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from alfred.adapters.git.commits import CommitResult, PushResult
from alfred.adapters.git.worktrees import WorktreeStatus
from alfred.cli.run_commands import handle_run, handle_worktree
from alfred.domain.constants import CompletionStatus, RunStatus, TaskStatus
from alfred.domain.models import AgentRun, CompletionReport, MergeRequestOptions, Task


class FakeRuns:
    def __init__(self) -> None:
        self.run = AgentRun("run-1", 7, "builder", "local", RunStatus.RUNNING)

    def trigger(self, task_numbers, *, parallel, actor):
        return (self.run,)

    def list(self):
        return (self.run,)

    def stop(self, task_number, reason, *, cleanup, force):
        self.run.run_status = RunStatus.STOPPED
        return self.run

    def continue_execution(self, task_number, note, *, actor):
        return self.run

    def active(self, task_number):
        return self.run

    def session_names(self):
        return (self.run.session_name,) if self.run.session_name else ()

    def record_event(self, task_number, event_type, note, *, actor, override_actor):
        return Task(task_number, "Example", "Details")

    def complete(self, task_number, result, summary, *, actor, override_actor):
        return CompletionReport(
            task_number,
            "builder",
            CompletionStatus(result),
            summary,
        )

    def reopen(self, task_number, *, actor):
        return self.run


class FakeWorktrees:
    def create(self, task, repositories):
        return {"app": Path("/tmp/worktrees/app")}

    def statuses(self, task_number):
        return (WorktreeStatus("app", Path("/tmp/worktrees/app"), "feature/x", ""),)


class FakeCommits:
    def commit(self, task_number, commit_type, message, *, repository=None):
        return (CommitResult("app", "abc123", "[PATCH] Small change"),)

    def __init__(self) -> None:
        self.merge_requests = []

    def push(self, task_number, *, repository=None, merge_request=None):
        self.merge_requests.append(merge_request)
        links = ("https://git.example.invalid/app/-/merge_requests/3",) if merge_request else ()
        return (PushResult("app", "origin", "feature/x", links),)


class RunCommandTests(unittest.TestCase):
    def output(self, callback, args, services) -> tuple[int, str]:
        stream = StringIO()
        with redirect_stdout(stream):
            code = callback(args, services)
        return code, stream.getvalue()

    def test_trigger_and_list_render_runs(self) -> None:
        task = Task(7, "Example", "Details", status=TaskStatus.QUEUED)
        services = SimpleNamespace(
            runs=FakeRuns(),
            tasks=SimpleNamespace(list=lambda: (task,)),
        )
        trigger = Namespace(
            action="trigger",
            all=True,
            tasks="",
            parallel=1,
            actor="manager",
        )
        self.assertIn("run-1", self.output(handle_run, trigger, services)[1])
        listing = Namespace(action="list", status="running")
        self.assertIn("task=7", self.output(handle_run, listing, services)[1])

    def test_trigger_reports_tasks_left_by_the_parallel_limit(self) -> None:
        services = SimpleNamespace(runs=FakeRuns())
        trigger = Namespace(
            action="trigger", all=False, tasks="7,8,9,8", parallel=1, actor="manager"
        )
        output = self.output(handle_run, trigger, services)[1]
        self.assertIn("Not dispatched (--parallel 1): 8, 9", output)

    def test_worktree_status_commit_and_push_render_results(self) -> None:
        services = SimpleNamespace(
            tasks=SimpleNamespace(require=lambda number: Task(number, "Example", "Details")),
            worktrees=FakeWorktrees(),
            commits=FakeCommits(),
        )
        status = Namespace(action="status", task=7)
        self.assertIn("clean", self.output(handle_worktree, status, services)[1])
        commit = Namespace(
            action="commit",
            task=7,
            type="PATCH",
            message="Small change",
            repo=None,
        )
        self.assertIn("abc123", self.output(handle_worktree, commit, services)[1])
        push = Namespace(
            action="push", task=7, repo=None, merge_request=False, title="", description=""
        )
        self.assertIn("origin", self.output(handle_worktree, push, services)[1])
        self.assertIsNone(services.commits.merge_requests[-1])

    def test_worktree_push_can_open_a_merge_request_titled_after_the_task(self) -> None:
        services = SimpleNamespace(
            tasks=SimpleNamespace(require=lambda number: Task(number, "Example", "Details")),
            commits=FakeCommits(),
        )
        push = Namespace(
            action="push", task=7, repo=None, merge_request=True, title="", description="Why"
        )
        output = self.output(handle_worktree, push, services)[1]
        self.assertEqual(
            services.commits.merge_requests,
            [MergeRequestOptions(title="Example", description="Why")],
        )
        self.assertIn("app: review https://git.example.invalid/app/-/merge_requests/3", output)

    def test_run_lifecycle_actions_render_results(self) -> None:
        runs = FakeRuns()
        runs.run.session_name = "alfred-task-7-builder"
        services = SimpleNamespace(runs=runs)
        commands = (
            Namespace(action="stop", task=7, reason="Pause", cleanup="no", force=False),
            Namespace(action="continue", task=7, note="Approved", actor="manager"),
            Namespace(action="attach", task=7),
            Namespace(action="sessions", task=7),
            Namespace(
                action="event",
                task=7,
                type="progress",
                note="Halfway",
                actor="agent:builder",
                override_manager=False,
            ),
            Namespace(
                action="complete",
                task=7,
                result="success",
                status_alias=None,
                note="Done",
                summary_alias="",
                actor="agent:builder",
                override_manager=False,
            ),
            Namespace(action="reopen", task=7, actor="manager"),
        )
        for command in commands:
            with self.subTest(action=command.action):
                self.assertEqual(self.output(handle_run, command, services)[0], 0)

    def test_worktree_create_and_nothing_to_commit(self) -> None:
        services = SimpleNamespace(
            tasks=SimpleNamespace(require=lambda number: Task(number, "Example", "Details")),
            worktrees=FakeWorktrees(),
            commits=SimpleNamespace(commit=lambda *args, **kwargs: ()),
        )
        create = Namespace(action="create", task=7, repos="app")
        self.assertIn("/tmp/worktrees/app", self.output(handle_worktree, create, services)[1])
        commit = Namespace(
            action="commit",
            task=7,
            type="PATCH",
            message="No changes",
            repo=None,
        )
        self.assertIn("Nothing to commit", self.output(handle_worktree, commit, services)[1])


if __name__ == "__main__":
    unittest.main()

"""Bracket-tag commit adapter integration tests."""

import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from alfred.adapters.git import GitCommitManager, GitWorktreeManager
from alfred.adapters.process import SubprocessRunner
from alfred.config.models import CommitConfig, RepositoryConfig, WorkspaceConfig
from alfred.domain.models import MergeRequestOptions, Task
from alfred.ports.process import ProcessResult

GITLAB_PUSH_OUTPUT = """\
remote:
remote: View merge request for feature/task-7:
remote:   https://git.example.invalid/team/api/-/merge_requests/12
remote:
To https://git.example.invalid/team/api.git
"""


class PushRecordingRunner:
    """Run Git for real, but record pushes and answer them like a GitLab remote."""

    def __init__(self, delegate: SubprocessRunner) -> None:
        self.delegate = delegate
        self.pushes: list[tuple[str, ...]] = []

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None = None,
        check: bool = True,
    ) -> ProcessResult:
        if arguments[:2] == ("git", "push"):
            self.pushes.append(arguments)
            return ProcessResult(arguments, 0, "", GITLAB_PUSH_OUTPUT)
        return self.delegate.run(arguments, cwd=cwd, check=check)


class GitCommitManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "api"
        self.repository.mkdir()
        self.runner = SubprocessRunner()
        self.runner.run(("git", "init", "-b", "main"), cwd=self.repository)
        self.runner.run(("git", "config", "user.name", "Test"), cwd=self.repository)
        self.runner.run(
            ("git", "config", "user.email", "test@example.invalid"), cwd=self.repository
        )
        self.repository.joinpath("README.md").write_text("example\n")
        self.runner.run(("git", "add", "README.md"), cwd=self.repository)
        self.runner.run(("git", "commit", "-m", "initial"), cwd=self.repository)
        self.workspace = WorkspaceConfig(
            root=self.root,
            repositories=(
                RepositoryConfig(
                    name="api",
                    path=self.repository,
                    selected_by_default=True,
                ),
            ),
        )
        self.worktrees = GitWorktreeManager(self.workspace, self.root / "worktrees", self.runner)
        self.task = Task(
            task_number=7,
            title="Example",
            description="A task",
            branch_name="feature/task-7",
        )
        self.path = self.worktrees.create(self.task)["api"]
        self.commits = GitCommitManager(
            self.worktrees,
            self.workspace,
            CommitConfig(tags=MappingProxyType({"PATCH": "[PATCH]"})),
            self.runner,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_creates_exact_configured_commit_message(self) -> None:
        self.path.joinpath("feature.txt").write_text("implemented\n")
        result = self.commits.commit(7, "patch", "Add example behavior")
        self.assertEqual(result[0].message, "[PATCH] Add example behavior")
        log = self.runner.run(("git", "log", "-1", "--format=%s"), cwd=self.path).stdout.strip()
        self.assertEqual(log, "[PATCH] Add example behavior")

    def test_clean_worktree_creates_no_commit(self) -> None:
        self.assertEqual(self.commits.commit(7, "PATCH", "Nothing changed"), ())

    def test_unknown_type_is_rejected_before_staging(self) -> None:
        self.path.joinpath("feature.txt").write_text("implemented\n")
        with self.assertRaisesRegex(ValueError, "configured types: PATCH"):
            self.commits.commit(7, "OTHER", "Add example behavior")
        staged = self.runner.run(
            ("git", "diff", "--cached", "--name-only"), cwd=self.path
        ).stdout.strip()
        self.assertEqual(staged, "")

    def test_repository_filter_must_match_task_worktree(self) -> None:
        with self.assertRaisesRegex(ValueError, "no worktree"):
            self.commits.commit(7, "PATCH", "Example", repository="missing")

    def test_push_reaches_a_real_bare_remote_without_options(self) -> None:
        remote = self.root / "remote.git"
        self.runner.run(("git", "init", "--bare", str(remote)), cwd=self.root)
        self.runner.run(("git", "remote", "add", "origin", str(remote)), cwd=self.repository)
        self.path.joinpath("feature.txt").write_text("implemented\n")
        self.commits.commit(7, "PATCH", "Add example behavior")

        result = self.commits.push(7)

        self.assertEqual(
            (result[0].branch, result[0].remote, result[0].review_url),
            ("feature/task-7", "origin", ""),
        )
        heads = self.runner.run(("git", "ls-remote", "--heads", str(remote)), cwd=self.root)
        self.assertIn("refs/heads/feature/task-7", heads.stdout)

    def test_merge_request_push_sends_gitlab_options_and_returns_the_link(self) -> None:
        recorder = PushRecordingRunner(self.runner)
        commits = GitCommitManager(self.worktrees, self.workspace, self.commits.commits, recorder)

        result = commits.push(
            7, merge_request=MergeRequestOptions(title="Add\nexample", description="Task 7")
        )

        self.assertEqual(
            recorder.pushes,
            [
                (
                    "git",
                    "push",
                    "-o",
                    "merge_request.create",
                    "-o",
                    "merge_request.target=main",
                    "-o",
                    "merge_request.title=Add example",
                    "-o",
                    "merge_request.description=Task 7",
                    "origin",
                    "feature/task-7",
                ),
            ],
        )
        self.assertEqual(
            result[0].review_url, "https://git.example.invalid/team/api/-/merge_requests/12"
        )
        self.assertEqual(len(result[0].links), 1)

    def test_plain_push_still_reports_links_the_remote_printed(self) -> None:
        recorder = PushRecordingRunner(self.runner)
        commits = GitCommitManager(self.worktrees, self.workspace, self.commits.commits, recorder)

        result = commits.push(7)

        self.assertEqual(recorder.pushes, [("git", "push", "origin", "feature/task-7")])
        self.assertTrue(result[0].review_url.endswith("/merge_requests/12"))


if __name__ == "__main__":
    unittest.main()

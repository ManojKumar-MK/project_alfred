"""Configurable commit and explicit push operations for task worktrees."""

import re
from dataclasses import dataclass

from alfred.adapters.git.worktrees import GitWorktreeManager, WorktreeStatus
from alfred.config.models import CommitConfig, WorkspaceConfig
from alfred.domain.models import MergeRequestOptions
from alfred.ports.process import ProcessRunner


@dataclass(frozen=True, slots=True)
class CommitResult:
    """One commit created in a task worktree."""

    repository: str
    revision: str
    message: str


@dataclass(frozen=True, slots=True)
class PushResult:
    """One branch explicitly pushed to a configured remote."""

    repository: str
    remote: str
    branch: str
    links: tuple[str, ...] = ()

    @property
    def review_url(self) -> str:
        """The merge or pull request link the remote printed, if any."""
        for marker in REVIEW_LINK_MARKERS:
            for link in self.links:
                if marker in link:
                    return link
        return ""


# Path fragments of the review links GitLab, Bitbucket, and GitHub print after a push.
REVIEW_LINK_MARKERS = ("/merge_requests/", "/pull-requests/", "/pull/")
URL_PATTERN = re.compile(r"https?://\S+")


class GitCommitManager:
    """Create configured bracket-tag commits and explicit branch pushes."""

    def __init__(
        self,
        worktrees: GitWorktreeManager,
        workspace: WorkspaceConfig,
        commits: CommitConfig,
        runner: ProcessRunner,
    ) -> None:
        self.worktrees = worktrees
        self.workspace = workspace
        self.commits = commits
        self.runner = runner

    def commit(
        self,
        task_number: int,
        commit_type: str,
        message: str,
        *,
        repository: str | None = None,
    ) -> tuple[CommitResult, ...]:
        """Stage and commit dirty selected task worktrees."""
        normalized_type = commit_type.upper().strip()
        tag = self.commits.tags.get(normalized_type)
        if tag is None:
            valid = ", ".join(sorted(self.commits.tags))
            raise ValueError(f"Unknown commit type {commit_type!r}; configured types: {valid}")
        summary = message.strip()
        if not summary:
            raise ValueError("Commit message cannot be empty")
        full_message = f"{tag} {summary}"

        results: list[CommitResult] = []
        for status in self._selected_statuses(task_number, repository):
            if not status.changes:
                continue
            self.runner.run(("git", "add", "-A"), cwd=status.path)
            self.runner.run(("git", "commit", "-m", full_message), cwd=status.path)
            revision = self.runner.run(
                ("git", "rev-parse", "--short", "HEAD"), cwd=status.path
            ).stdout.strip()
            results.append(
                CommitResult(
                    repository=status.repository,
                    revision=revision,
                    message=full_message,
                )
            )
        return tuple(results)

    def push(
        self,
        task_number: int,
        *,
        repository: str | None = None,
        merge_request: MergeRequestOptions | None = None,
    ) -> tuple[PushResult, ...]:
        """Push selected task branches to their explicitly configured remotes.

        With ``merge_request``, GitLab push options open (or reuse) a merge request against the
        repository's default branch. Links the remote prints are returned either way.
        """
        results: list[PushResult] = []
        for status in self._selected_statuses(task_number, repository):
            config = self.workspace.repository(status.repository)
            options = (
                _merge_request_options(merge_request, config.default_branch)
                if merge_request is not None
                else ()
            )
            result = self.runner.run(
                ("git", "push", *options, config.remote, status.branch),
                cwd=status.path,
            )
            results.append(
                PushResult(
                    repository=status.repository,
                    remote=config.remote,
                    branch=status.branch,
                    links=_remote_links(result.stderr),
                )
            )
        return tuple(results)

    def _selected_statuses(
        self,
        task_number: int,
        repository: str | None,
    ) -> tuple[WorktreeStatus, ...]:
        statuses = self.worktrees.statuses(task_number)
        if repository is None:
            return statuses
        selected = tuple(status for status in statuses if status.repository == repository)
        if not selected:
            raise ValueError(f"Task {task_number} has no worktree for repository {repository!r}")
        return selected


def _merge_request_options(options: MergeRequestOptions, target: str) -> tuple[str, ...]:
    """Render GitLab ``merge_request.*`` push options; values must be single lines."""
    values = [("create", ""), ("target", target)]
    if options.title.strip():
        values.append(("title", _single_line(options.title)))
    if options.description.strip():
        values.append(("description", _single_line(options.description)))
    rendered: list[str] = []
    for key, value in values:
        rendered.extend(("-o", f"merge_request.{key}={value}" if value else f"merge_request.{key}"))
    return tuple(rendered)


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _remote_links(stderr: str) -> tuple[str, ...]:
    """Return the distinct URLs a remote printed on ``remote:`` lines, in order."""
    links: list[str] = []
    for line in stderr.splitlines():
        if not line.startswith("remote:"):
            continue
        for match in URL_PATTERN.findall(line):
            link = match.rstrip(".,)")
            if link not in links:
                links.append(link)
    return tuple(links)

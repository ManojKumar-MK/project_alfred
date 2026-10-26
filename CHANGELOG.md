# Changelog

All notable changes will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project does not
claim Semantic Versioning compatibility until its first supported release.

## Unreleased

### Added

- Released under the MIT License.
- Typed project-local TOML configuration and versioned atomic JSON state.
- Task, agent, run, worktree, plan-approval, completion, notification, and knowledge workflows.
- Optional transactional Markdown tracker synchronization.
- Non-destructive legacy JSON migration with backups and idempotent markers.
- macOS-oriented installable CLI, strict quality checks, and public-project documentation.
- Complete operator-flow documentation and an isolated end-to-end release test runbook.
- `task cancel` abandons a task at any non-terminal stage; `task rename-branch` renames the task
  branch and its worktree branches; `research` and `analysis` task types need no branch.
- Reassigning an agent saves the old session's transcript and hands a summary to the next agent;
  transcripts are also saved on stop and completion and offered to the learner.
- Per-task `--model` with a `{model}` command placeholder, and editable per-phase skills
  (`alfred skill`).
- `worktree push --merge-request` opens a GitLab merge request with push options and prints the
  review link the remote reports.
- UI: a pull-request review tab with line comments, approve, and request changes that hands the
  comments to the agent; local pipelines from `pipelines.toml` with live step logs; an inbox bell;
  desktop alerts for every "needs you" item; and a splash screen.

### Fixed

- Triggering a task with an active run no longer creates a second run; a queued run is superseded.
- Merge requires an approved task, deploy requires a merge, and neither they nor archive or
  consolidate run while an agent run is still active; stopping a run never reopens a terminal task.
- An `unblocked` event resumes the blocked run, and dead sessions are reported once.
- Blocked and failed completions produce `task_blocked` and `task_failed` notifications.
- Invalid session prefixes are rejected at load, and the learner session is workspace-scoped.
- A completed run is recorded only after its task update and completion report are written, so
  a finished run is never visible before its coordinator handoff.

### Security

- Shell-free subprocess argument handling and validated repository/session identifiers.
- Explicit-only push and forced dirty-worktree cleanup.
- Generated lifecycle commands identify the assigned agent and include required completion details.
- Queued runs can be stopped without tmux, and session listings are scoped to Alfred's prefix.
- Repository `default_branch` and `remote` values and new task branch names must be valid Git
  names that cannot be read as options; worktree creation refuses an unsafe stored branch.

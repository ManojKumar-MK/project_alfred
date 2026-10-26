# User and operator flows

This document is the source of truth for Alfred's observable workflows. It describes what an
operator does, what Alfred changes, and how to recover when a flow stops. For a release-candidate
walkthrough that exercises these flows, see [End-to-end release testing](end-to-end-testing.md).

## Flow map

```text
initialize -> configure -> create task -> assign agent
                                      |
                                      +-> direct execution ------+
                                      |                           |
                                      +-> plan -> approve -> run -+-> completion report
                                                                  |
                                                                  v
                                      human notification <- coordinator
                                                |
                                                v
                                  review -> merge -> deploy -> archive
```

Supporting flows operate alongside this lifecycle:

- Git worktrees isolate repository changes and provide explicit commit and push commands.
- Task events, JSON state, and optional Markdown trackers preserve an audit trail.
- Knowledge entries and the learner turn completed work into reusable project notes.
- Reports project the current task state for daily review, risk, dependencies, and velocity.
- Migration imports legacy JSON state without deleting its source.

## 1. Initialize and load a workspace

Initialize once at the root of the project Alfred will manage:

```console
alfred init --root /path/to/project
```

This creates `.alfred/config.toml`, `.alfred/state/`, `.alfred/tmp/`, and
`.alfred/worktrees/`. It refuses to overwrite an existing config unless `--force` is supplied.
`--force` replaces only the configuration file; treat it as an explicit configuration reset.

Every command except `init` loads configuration in this order:

1. `--config PATH`
2. `ALFRED_CONFIG`
3. the nearest `.alfred/config.toml` found while walking from the current directory upward

Loading configuration also creates any missing versioned state documents. It does not start an
agent, create a worktree, or access a network.

Before creating tasks, configure at least one repository and one agent. See
[Configuration](configuration.md) for every setting. Agent commands are argument arrays and are
never evaluated by a shell.

## 2. Create and maintain a task

A worktree-enabled task needs a branch. It can select repositories explicitly with `--repos`, or
fall back to repositories whose `selected_by_default` setting is true.

```console
alfred task create \
  --task 42 \
  --title "Add health endpoint" \
  --description "Implement and validate the endpoint" \
  --priority P2 \
  --branch feature/health \
  --repos app \
  --assign builder
```

Important creation choices:

| Choice | Behavior |
|---|---|
| `--mode direct` | The first dispatch creates worktrees and sends an execution prompt. |
| `--mode plan-execution` | The first dispatch sends only a planning prompt; worktrees are deferred until approval. |
| `--worktree enabled` | A branch and one or more configured repositories are required at dispatch. |
| `--worktree disabled` | The agent runs from the workspace root; a branch is optional. |
| `--dispatch auto` | The task starts as `Pending` and is triggered explicitly by task number. |
| `--dispatch queued` | The task starts as `Queued` and is eligible for `run trigger --all`. |

`task create` and `task update` reject agent aliases and repository names that are not
configured. Use `task update` to change task details before or during work. Changing the execution mode resets
the planning state to the correct initial value. Dependencies are descriptive and appear in
reports; they do not automatically block dispatch.

```console
alfred task update --task 42 --priority P1 --dependencies 12,18
alfred task start --task 42 --actor manager
alfred task progress --task 42 --note "Endpoint implemented" --actor agent:builder
```

Each mutation validates the whole task, atomically updates JSON task and event state, and then
updates the optional Markdown tracker. If tracker writing fails, the JSON task and event changes
are rolled back.

### Status and lifecycle model

Task status describes the work state. Lifecycle phase describes where the task sits in the wider
delivery process.

```text
Pending/Queued -> Running/In Progress -> MR in Review -> Completed
       |                |                    |
       +-> Blocked -----+--------------------+

active -> testing_deployment -> archived
   |
   +-> consolidated
```

The state machine rejects invalid transitions. `Completed` and `Consolidated` are terminal task
statuses; `archived` and `consolidated` are terminal lifecycle phases.

## 3. Assign or reassign an agent

```console
alfred agent assign --task 42 --to builder
alfred agent status --task 42
```

Only aliases defined in `.alfred/config.toml` are accepted.

Reassignment has two modes:

- `soft-switch` changes the task's assigned alias but does not stop or replace an existing session.
  Use it only when the running session and the new actor have been coordinated manually.
- `stop-and-switch` stops the active run first, resets the task to `Pending`, and then assigns the
  new alias.

```console
alfred agent reassign --task 42 --to reviewer --mode stop-and-switch
```

## 4. Direct execution

Preconditions are a configured agent, a valid task, and—when worktrees are enabled—a branch plus
selected repositories.

```console
alfred run trigger --tasks 42
alfred run list --status running
alfred run sessions --task 42
alfred run attach --task 42
```

For direct execution Alfred:

1. creates or reuses each task worktree;
2. renders the agent's `commands.execution` argument array;
3. writes `.alfred/tmp/prompts/task-42-execution.md`;
4. creates or reuses the task's tmux session;
5. delivers the prompt — as a command argument when the agent command uses `{prompt}` or
   `{prompt_file}`, otherwise by bracketed paste (see [Configuration](configuration.md));
6. persists the run and records `RUN_STARTED`.

`run attach` prints a `tmux attach-session` command; it does not replace the current process with
tmux. `--parallel N` limits how many supplied tasks are started by that invocation; the tasks it
leaves undispatched are listed as `Not dispatched (--parallel N): ...`. Duplicate task numbers are
removed while preserving order.

Triggering a task that already has a running or blocked run is rejected before any worktree or
session is touched; stop that run first, or use `run reopen` after it has finished.

## 5. Plan, approve, and execute

Create the task with `--mode plan-execution`, then trigger it:

```console
alfred run trigger --tasks 43
```

The plan phase runs from the workspace root and creates no worktree. An agent may record that its
plan is ready:

```console
alfred run event \
  --task 43 \
  --type plan_completed \
  --note "Plan is ready for review" \
  --actor agent:builder
```

After human approval, continue the same run:

```console
alfred run continue --task 43 --note "Plan approved" --actor manager
```

Continuation changes planning state from `started` to `completed`, passes the `--note` to the
agent as the task's latest note, creates the configured worktrees, renders `commands.execution`, and reuses the same session when it still exists. Calling
`continue` for a direct task, a task without an active plan, or an already-continued plan fails.

`run event --type plan_approved` is an actor-checked alternative that invokes the same continuation
flow. Normally the manager should use `run continue`; use the event form only when an authorized
agent is responsible for that transition.

## 6. Queue when tmux is unavailable

With `tmux_unavailable_policy = "queue"`, dispatch still writes the prompt and run state when tmux
cannot be found. The run and task become `queued`; no process has started. With policy `error`, the
dispatch fails instead.

The queue is persistent, not an automatic background scheduler. After tmux becomes available,
trigger the task again; the new dispatch supersedes the queued run (recorded as `stopped`) rather
than adding a second active run. A queued attempt can also be cancelled—even while tmux is still
unavailable:

```console
alfred run trigger --tasks 42
alfred run stop --task 42 --reason "Cancel queued attempt" --cleanup no
```

`run trigger --all` selects tasks whose task status is `Queued`.

## 7. Report progress, blockers, and review readiness

Agent-originated run events require `--actor agent:<assigned-alias>` unless a manager deliberately
uses `--override-manager`.

```console
alfred run event --task 42 --type progress --note "Tests are running" --actor agent:builder
alfred run event --task 42 --type blocked --note "Waiting for fixture" --actor agent:builder
alfred run event --task 42 --type unblocked --note "Fixture received" --actor agent:builder
alfred run event --task 42 --type review_requested --note "Ready for review" --actor agent:builder
```

Supported event types are `plan_completed`, `plan_approved`, `progress`, `coding`,
`execution_started`, `blocked`, `unblocked`, `review_requested`, and `fixing`.

Manager-oriented task commands provide the same visible lifecycle controls without requiring an
active run:

```console
alfred task block --task 42 --reason "External dependency unavailable"
alfred task unblock --task 42 --note "Dependency restored"
```

## 8. Complete, coordinate, and notify

The assigned agent reports a non-empty summary:

```console
alfred run complete \
  --task 42 \
  --result success \
  --note "Implemented and validated the endpoint" \
  --actor agent:builder
```

Completion results map to state as follows:

| Result | Run | Task | Intended next action |
|---|---|---|---|
| `success` | `completed` | `MR in Review` | Human review |
| `failed` | `failed` | `In Progress` | Fix and `run reopen` |
| `blocked` | `blocked` | `Blocked` | Resolve, record `unblocked` (the run returns to `running`), and continue the same run |

Completion writes `.alfred/tmp/completions/pending/task-42.json`. The coordinator processes that
handoff, validates the knowledge-entry count, archives it under `processed/`, and creates a
persistent human notification typed `task_completed`, `task_failed`, or `task_blocked`. The
knowledge-entry minimum is checked only for successful completions. A later completion of the same task is archived alongside the
earlier one (`task-42-2.json`, and so on) rather than replacing it. `alfred notifications` prints
each notification's status, agent, repositories, and any validation issues.

Completion marks the persisted session inactive but intentionally does not stop the underlying tmux
session. That permits a later attempt to reuse it. After the final review/archive, inspect and stop
the task's tmux session manually; Alfred currently has no terminal-run session cleanup command.

```console
alfred coordinator once
alfred notifications
alfred notifications ack --task 42
alfred notifications clear
```

Malformed completion files are moved to `completions/invalid/`. A background coordinator can poll
every five seconds:

```console
alfred coordinator start
alfred coordinator status
alfred coordinator stop
```

It also reports a `session_died` notification when a persisted running run no longer has a live
tmux session, and marks that run's session `dead` so the alert is raised once even after it is
acknowledged or cleared. The run stays active: stop it, then reopen the task to retry.

## 9. Stop or reopen a run

Stop closes a live tmux session, marks the run `stopped`, removes the task from the queue, resets the
task to `Pending`, and resets a plan-execution task to planning state `pending`. A task that is
already `Completed` or `Consolidated` keeps its status; stop then only closes the stale run.

```console
alfred run stop --task 42 --reason "Superseded approach" --cleanup no
```

Use `--cleanup yes` to remove task worktrees. Dirty worktrees fail closed unless `--force` is also
provided; the check runs before the session is stopped, so a refused cleanup leaves the run and its
agent session untouched. In a non-interactive shell, the default `--cleanup ask` behaves as `no`.

After a stopped, completed, or failed run has no active attempt, create another execution attempt.
For a plan-execution task this starts the execution phase directly, even after `stop` reset its
planning state:

```console
alfred run reopen --task 42 --actor manager
```

A blocked run remains active. Unblock it and continue reporting events instead of reopening it.

## 10. Worktree, commit, and push

Worktrees are stored under the configured directory as `task-<number>/<repository>`. Alfred creates
a branch from the repository's configured default branch, or reuses an existing non-diverged local
branch. A mismatched existing worktree, missing repository, or branch diverged on both sides is
rejected.

```console
alfred worktree create --task 42 --repos app,library
alfred worktree status --task 42
alfred worktree commit --task 42 --type feature --message "Add health endpoint"
alfred worktree push --task 42 --repo app
```

Commit stages all changes in each selected dirty task worktree and applies the configured tag, such
as `[FEATURE]`. A clean worktree prints `Nothing to commit`. Push is the only Alfred operation that
mutates a remote and always names the configured remote and current branch explicitly.

To open a merge request on a GitLab remote in the same step, add `--merge-request`:

```console
alfred worktree push --task 42 --repo app --merge-request --title "Add health endpoint"
```

Alfred passes GitLab's `merge_request.create` and `merge_request.target=<default branch>` push
options (plus `title` and `description` when given; the title defaults to the task title). GitLab
opens the merge request, or reuses the open one for the branch, and Alfred prints the link it
reports. Any merge or pull request link a remote prints after a plain push, such as Bitbucket's or
GitHub's, is printed too. No API token is involved.

Cleanup is currently available through `run stop --cleanup yes`; perform it before making the run
terminal. If a completed task still has a worktree, inspect and remove it with Git only after the
branch and uncommitted work have been reviewed. Terminal session and worktree cleanup limitations
must be included in release testing.

## 11. Human review, merge, deploy, archive, or consolidate

The normal success path is:

```console
alfred task review --task 42 --decision approved --note "Review passed"
alfred task merge --task 42 --mr 123
alfred task deploy --task 42 --env sandbox --result passed
alfred task archive --task 42 --note "Sandbox and production checks complete"
```

`review --decision changes_requested` returns the task to `In Progress`. `merge` requires a
`review --decision approved` recorded after the task's latest run, progress, or status change (an
agent's successful completion alone is not an approval) and moves lifecycle phase to
`testing_deployment`. `deploy` requires
that phase and marks the task `Completed`; it records the supplied environment and result but does
not invoke a deployment tool. `archive` is allowed only from `testing_deployment`. Merge, deploy,
archive, and consolidate are refused while the task still has a queued, running, or blocked run.

Use consolidation instead when a live task is absorbed into another task:

```console
alfred task consolidate --task 42 --note "Covered by task 57"
```

Consolidation is terminal and is not part of the merge/deploy/archive path.

## 12. Optional Markdown tracker and reconciliation

When enabled, every task mutation updates:

- one canonical task table;
- one agent-assignment table;
- a dated daily-note timeline.

Validate without changing files:

```console
alfred sync validate
alfred sync drift-report --task 42
```

Both validation commands return exit code 1 when paths, task rows, or agent rows are missing.
Reconcile one task or all tasks explicitly:

```console
alfred sync apply --task 42
alfred sync apply
```

`apply` records `SYNC_APPLIED` and sends the current task through the normal transactional tracker
update. With tracking disabled, validation reports that fact and apply is rejected.

## 13. Knowledge and learner

Write reusable project knowledge into one of `patterns`, `decisions`, `entities`, `issues`, or
`conventions`:

```console
alfred knowledge add \
  --task 42 \
  --category patterns \
  --title "Health check contract" \
  --content "Health checks return no customer data." \
  --agent builder \
  --files src/health.py \
  --modules app

alfred knowledge list --category patterns
```

The completion report records how many entries match the task. If
`required_completion_entries` is greater than that count, the coordinator still processes the
report but adds a validation issue to its notification. Failed and blocked completions are not
held to this minimum.

The optional learner is a separate tmux session named `<session_prefix>-learner`, so each
workspace sharing a tmux server has its own. It receives paths to processed completion reports and
the knowledge directory:

```console
alfred learner start --agent builder
alfred learner status
alfred learner attach
alfred learner stop
```

Like `run attach`, `learner attach` prints the tmux command rather than attaching automatically.
The learner is told to add entries only and never to edit, merge, or delete existing ones, because
knowledge entries are counted per task for completion validation.

## 14. Reports

Reports are read-only projections of current task state:

```console
alfred report today
alfred report risk
alfred report dependency
alfred report velocity
```

- `today` lists non-terminal tasks.
- `risk` lists active (not completed or consolidated) blocked, on-hold, P0, and P1 tasks.
- `dependency` lists tasks declaring dependencies.
- `velocity` prints completed-or-consolidated tasks over total tasks.

## 15. Migrate legacy runtime state

Run migration only against a newly initialized destination whose task, run, and queue documents are
empty:

```console
alfred migrate --source /path/to/legacy/data/runtime
```

Recognized source files are `tasks.json`, `runs.json`, `queue.json`, and `agent_map.json`. Alfred
validates the full source first, copies source files to a timestamped backup, writes normalized
state, writes an idempotency marker, and leaves the legacy directory unchanged. Legacy agent shell
templates are converted to a TOML fragment for manual review; Alfred never merges them into active
configuration automatically.

## 16. Runtime files and ownership

| Location | Contents | Owner/action |
|---|---|---|
| `.alfred/config.toml` | Project-local configuration | Human-reviewed; private by default |
| `.alfred/state/*.json` | Tasks, runs, queue, events, notifications | Alfred; atomic and versioned |
| `.alfred/tmp/prompts/` | Rendered task and learner prompts | Sensitive local runtime data |
| `.alfred/tmp/completions/` | Pending, processed, and invalid handoffs | Coordinator input/audit data |
| `.alfred/worktrees/` | Per-task Git worktrees | Alfred creates; operator reviews cleanup |
| `.alfred/knowledge/` | Categorized Markdown learnings | Agent or operator authored |
| configured tracker paths | Task, agent, and daily Markdown | Optional transactional integration |

Do not commit these files without deliberate review. Prompts, completions, trackers, and knowledge
can contain repository paths, task details, or other private information.

## Command surface

| Group | Actions |
|---|---|
| `init` | initialize a workspace |
| `task` | `create`, `update`, `start`, `progress`, `block`, `unblock`, `review`, `merge`, `deploy`, `archive`, `consolidate` |
| `agent` | `assign`, `reassign`, `status` |
| `run` | `trigger`, `list`, `stop`, `continue`, `attach`, `sessions`, `event`, `complete`, `reopen` |
| `worktree` | `create`, `status`, `commit`, `push` |
| `sync` | `validate`, `drift-report`, `apply` |
| `knowledge` | `add`, `list` |
| `report` | `today`, `risk`, `dependency`, `velocity` |
| `coordinator` | `start`, `stop`, `status`, `once`, `loop` |
| `learner` | `start`, `stop`, `status`, `attach` |
| `notifications` | list (no action), `ack`, `clear` |
| `migrate` | import a legacy runtime directory |

Use `alfred <group> <action> --help` for the authoritative option list of a particular command.

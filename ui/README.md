# Alfred UI

A local web interface for [Alfred](../README.md). It shows a live Kanban board of your tasks, lets
you watch and type into the agents' tmux sessions, and puts every Alfred workflow behind a button.
Each dialog also shows the equivalent `alfred` command.

The UI runs on your Mac next to the `alfred` CLI and works on the same `.alfred` workspace through
Alfred's own services. You can switch between the CLI and the browser at any time. Changes made
from a shell or by an agent appear in the browser within a second, without reloading.

> [!NOTE]
> The UI is pre-release and still under review before it is merged; see [CHECKLIST.md](CHECKLIST.md).
> Like the rest of this repository, it is released under the [MIT License](../LICENSE).

## What you can do

- **Board**: tasks as cards grouped by status, lifecycle phase, or agent, with filters for agent,
  priority, and repository. Drag a card onto a column to open that column's action; for example,
  dropping a card on Blocked asks for the reason. If Alfred doesn't allow a move, a message says
  why. The "Needs you" strip lists plans waiting for approval, reviews, failed and blocked runs,
  dead sessions, and completion reports waiting for the coordinator.
- **Task**: click a card to open the task in a drawer, or open it as a full page. It shows the
  next step and where the task is in its lifecycle. **Cancel task** abandons it at any stage
  (`alfred task cancel`), stopping a live run and optionally removing its worktrees. Creating or
  editing a task sets its type (development, or research and analysis, which run without a branch)
  and its model; editing the branch also renames it in existing worktrees. Assign and reassign can
  choose a model too, and a reassigned agent receives a handoff from the previous session. The task
  has these tabs:
  - **Overview**: description, latest note, details, and a live preview of the agent's screen.
  - **Plan**: for plan-first tasks, the plan the agent reported. Approve it (`alfred run continue`)
    or request changes.
  - **Agents and runs**: the assigned agent with its CLI, model, and exact commands; everyone who
    touched the task; every run.
  - **Terminal**: the agent's tmux session in the browser, either interactive or watch only.
  - **Changes**: each worktree's status, diff, commits, ahead/behind counts, and remote branch,
    with buttons to create, commit, push, and remove worktrees.
  - **Review**: a pull-request view of the task branch against the remote default branch (for
    example `origin/master`): a file tree, unified or side-by-side diffs, and comments on any line
    or file that you can reply to, edit, resolve, and reopen. **Approve** records
    `alfred task review --decision approved`. **Request changes** records the review and gives
    the agent every open comment: pasted into its live session, or, when the run has finished,
    by reopening the run, whose new prompt includes the feedback. **Push and open MR** pushes with
    `--merge-request`, and the merge request link is kept here for **Record merge**. Comments live
    under `.alfred/tmp/ui/reviews`.
  - **Pipelines**: checks you declare in `.alfred/pipelines.toml` (edit it under **Configure
    steps**) run in the task's worktrees, with live logs per step. Each step is an argument array,
    never a shell string; a repository's steps stop at its first failure, and a step that runs past
    `timeout_minutes` is stopped. The Review tab shows the latest result.
  - **Activity**: the task's full event history.
  - **Files**: the prompts sent to the agent, completion reports, saved transcripts, tracker rows,
    and knowledge entries.
- **Inbox**: the bell in the top bar lists what needs you and what was acknowledged earlier, with
  unread marks you can clear. **Desktop alerts** (Notifications or Settings) raise a system
  notification for each new item while the Alfred tab is in the background; clicking one opens the
  task. A splash screen with the Alfred mark shows until the first live update arrives.
- **Agent sessions**: every tmux session under the workspace's prefix. Screen previews use
  `capture-pane`, so watching never attaches to or resizes an agent. You can open a terminal, send
  a message or keys, and close sessions that finished runs leave behind.
- **Live feed**, **Runs**, **Notifications**, **Knowledge**, and **Reports**: live views of Alfred's
  events, runs and queue, coordinator notifications, knowledge base, and reports.
- **Operations**: run the coordinator once or in the background, start and stop the learner,
  inspect completion handoffs, check the Markdown tracker for drift and fix it, and read the raw
  state files.
- **Settings**: open or initialize a workspace, edit `config.toml` (validated by Alfred before it
  is saved), add agents from presets (Claude Code, Codex CLI, Antigravity), run health checks,
  edit the plan and execution skills appended to agent prompts (`alfred skill`), migrate legacy
  runtime state, and set the actor your actions are recorded as.

Press <kbd>⌘K</kbd> anywhere to jump to a task or an action.

## Requirements

- Everything Alfred needs: macOS, Python 3.11 or newer, Git, and tmux
- Node.js 20.19+ or 22.12+ to build the frontend
- A current browser

## Install

From this `ui/` directory:

```console
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .. -e '.[dev]'
cd web
npm ci
npm run build
```

Any Python 3.11 or newer works for the virtual environment. Name it explicitly as above:
macOS's own `/usr/bin/python3` is 3.9, and its pip can't install Alfred.

`alfred-ui` depends on Alfred (`project-alfred-cli`), which is not published. Installing `-e ..`
in the same command takes it from this repository. `npm run build` type-checks the frontend and
writes it to `src/alfred_ui/static/`, where the server finds it.

## Run

```console
.venv/bin/alfred-ui --config /path/to/project/.alfred/config.toml
```

The server prints a link like `http://127.0.0.1:8765/?token=…`. Open it once: the token becomes an
HttpOnly cookie and is removed from the address bar. A browser without the cookie gets a sign-in
page where you can paste the token.

Press Ctrl-C to stop the server. Agents and the background coordinator keep running in tmux, and
the UI picks them up again when you restart it.

### Updating a running server

**Settings → Server** shows which checkout the server runs, its Git branch and commit, and when the
server and the frontend build started. It warns when the Python code or the frontend sources on disk
are newer than what is running. **Update and restart** rebuilds the frontend (`npm run build` with
the installed `node_modules`) and restarts the server in place on the same port, workspace, and
token, so open browsers reconnect without signing in again. Agent sessions and the coordinator run
in tmux and keep running. The update never downloads anything: pull or switch branches yourself
first, and run `npm ci` or `pip install` yourself when dependencies change.

Without `--config`, the workspace is found the way `alfred` finds it: `$ALFRED_CONFIG`, then the
nearest `.alfred/config.toml` above the current directory. If none is found, the UI opens anyway
and Settings offers to open or initialize one.

| Option | Meaning |
| --- | --- |
| `--config PATH` | Workspace configuration to open |
| `--host HOST` | Interface to listen on (default `127.0.0.1`) |
| `--port N` | Port (default 8765, or the next free one; `0` picks any) |
| `--token TOKEN` | Access token (default `$ALFRED_UI_TOKEN`, else a new random one) |
| `--no-token` | Turn the token off; only for development on a trusted machine |
| `--open` | Open the link in your browser |
| `--allow-host NAME` | Accept another `Host` name (repeatable) |
| `--allow-origin ORIGIN` | Accept another browser origin (repeatable) |
| `--static-dir PATH` | Serve a different frontend build |
| `--log-level LEVEL` | Server log level (default `warning`) |

### Agents need `alfred` in their sessions

Agents report progress by running `alfred` in their tmux session. When `alfred-ui` starts the tmux
server, it adds its own `alfred` to the end of that server's `PATH`. If your tmux server was already
running and can't find `alfred`, **Settings → Health checks** says so and can fix it: the fix adds
the directory to the end of tmux's global `PATH` and never puts it first.

### Using Claude Code as an agent

The Claude Code preset puts `{prompt}` right after `claude`. Keep it there: `--allowedTools`
accepts several values, so a prompt placed after it would be read as another tool name.

In a new worktree, Claude Code first asks whether to trust the folder, and it may ask permission
for tools not listed in `--allowedTools`. Answer these prompts in the task's **Terminal** tab.
Check which option is highlighted before you press Enter: in Claude Code 2.1.280 the trust prompt
had "No, exit" highlighted, so you press ↓ and then Enter.

Start `alfred-ui` from an ordinary terminal. Agent sessions inherit its environment, so if you
start it inside a Claude Code session, the `CLAUDECODE` and `CLAUDE_CODE_*` variables are passed on
to the agents.

## Try it in a sandbox

`scripts/make_sandbox.py` builds a disposable workspace like the one in
[`docs/end-to-end-testing.md`](../docs/end-to-end-testing.md). It creates Git repositories with
local bare remotes, the Markdown tracker, a one-entry knowledge requirement, and two agents:

- `fake`: a scripted agent that runs real `alfred` commands
- `recorder`: an agent that only waits

```console
.venv/bin/python scripts/make_sandbox.py --root "$HOME/alfred-ui-sandbox" --seed
TMUX_TMPDIR="$(mktemp -d)" .venv/bin/alfred-ui \
  --config "$HOME/alfred-ui-sandbox/.alfred/config.toml"
```

`--seed` adds example tasks, `--second-repo` adds a second repository, and `--real-agents` adds
Claude Code and Codex agents. A private `TMUX_TMPDIR` keeps the sandbox's sessions off your usual
tmux server.

The fake agent follows markers in the task description:

| Marker | What the agent does |
| --- | --- |
| `[fake:wait]` | Waits until someone types `go` into its session |
| `[fake:fail]`, `[fake:fail-once]` | Reports a failure, always or only on the first run |
| `[fake:block]` | Reports that it is blocked |
| `[fake:commit]` | Commits its change instead of leaving it for you |
| `[fake:trust]` | Shows a trust prompt first, like Claude Code |
| `[fake:exit]` | Exits without reporting, leaving a dead session |
| `[fake:noknowledge]` | Completes without adding a knowledge entry |

## How it works

```text
browser (Vue 3)
  ├─ HTTP /api/...               ─▶  FastAPI app ─▶ Alfred services (build_services)
  ├─ WebSocket /api/live         ◀─  LiveHub: state files, handoffs, prompts, worktrees, tmux
  └─ WebSocket /api/terminal/…   ◀▶  tmux attach-session in a pseudo-terminal
```

- **Same rules as the CLI.** Every request builds Alfred's services from the workspace
  configuration, so validation, the lifecycle state machine, tracker sync with rollback, actor
  checks, and the approval rule behave exactly as in `alfred`. The server handles one mutation at a
  time.
- **Live updates.** The `/api/live` WebSocket sends a new snapshot whenever a state file,
  completion report, prompt, transcript, knowledge entry, tracker file, or worktree changes. It
  checks files every 0.4 seconds, tmux sessions every second, and worktrees every 4 seconds.
- **Terminals.** `/api/terminal/<session>` runs `tmux attach-session` (with `-r` for watch only)
  in a pseudo-terminal and streams it to xterm.js.
- **Transcripts.** When an agent reports a plan, a run completes, fails, or is blocked, a run is
  stopped, or a session is closed, the session's scrollback is saved under
  `<temp_directory>/ui/transcripts/task-<N>/`.
- **Beyond the CLI.** The UI can put a task on hold (Alfred's `On Hold` status), send text and keys
  to agents, and close sessions left behind by finished runs. It records these actions in the task's
  history as `OPERATOR_MESSAGE`, `SESSION_CLOSED`, and `WORKTREES_REMOVED` events.

## Security

The UI can type into your agents' terminals, so it guards every request:

- It listens on `127.0.0.1` unless you pass `--host`.
- API and WebSocket requests need the access token, sent as an `X-Alfred-Token` header or the
  HttpOnly, `SameSite=Strict` cookie.
- The `Host` header must name an allowed host, which blocks DNS rebinding.
- WebSocket connections and requests that change something must come from the server's own origin
  or one you allowed.
- Commands run as argument lists, never through a shell. Text for an agent goes through a tmux paste
  buffer loaded from a private temporary file, and only named keys can be sent.
- Alfred's safety rules still apply. Nothing is pushed until you press Push
  (`alfred worktree push`). Worktrees with uncommitted changes are removed only when you choose
  to discard the changes (`--force`). A session is never closed while its run is active.

If you listen on another interface, anyone who has the token can control your agents.

## Development

Run the backend, then the Vite dev server with hot reload. The dev server sends `/api` requests to
`http://127.0.0.1:8765`; set `ALFRED_UI_BACKEND` to use another address.

```console
.venv/bin/alfred-ui --config /path/to/.alfred/config.toml --port 8765
cd web && npm run dev    # http://127.0.0.1:5173, then paste the printed token to sign in
```

Quality checks:

```console
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/mypy
.venv/bin/pytest --cov=alfred_ui
cd web
npm run typecheck && npm test && npm run build
npx playwright install chromium    # first time only
npx playwright test
```

- **pytest** runs the API with real Git repositories, a private tmux server, and the fake agent.
  It covers the task, run, worktree, workspace, and operations workflows, the WebSockets and
  terminal bridge, the access guard, and the task summaries shown on cards.
- **Vitest** covers the frontend helpers: ANSI rendering, command lines and shell quoting, diffs,
  board columns and drop rules, next steps, and formatting.
- **Playwright** builds a sandbox, starts a private tmux server and a real `alfred-ui`, and drives
  Chromium through 26 scenarios. Each scenario checks its results with the `alfred` CLI, Git, and
  tmux. It covers the direct and plan-first lifecycles, failure and reopen, dead sessions, the queue
  fallback, the coordinator and learner, tracker drift, workspaces and migration, and sign-in.

### Layout

```text
ui/
├── pyproject.toml          alfred-ui package (FastAPI server)
├── src/alfred_ui/
│   ├── app.py              routes, error mapping, frontend serving
│   ├── actions.py          mutations, mirroring the CLI handlers
│   ├── snapshot.py         the live snapshot and per-task summaries
│   ├── live.py             change detection and the /api/live WebSocket
│   ├── terminal.py         PTY bridge to tmux attach-session
│   ├── tmux_inspector.py   read-only tmux queries, send text and keys
│   ├── gitinfo.py          worktree status, diffs, commits (read-only Git)
│   ├── doctor.py           health checks
│   ├── configedit.py       config validation, presets, snippets
│   ├── security.py         token, Host, and Origin checks
│   └── …
├── scripts/                make_sandbox.py and the scripted fake agent
├── tests/                  pytest suite
└── web/                    Vue 3 + TypeScript frontend, Vitest, and Playwright tests
```

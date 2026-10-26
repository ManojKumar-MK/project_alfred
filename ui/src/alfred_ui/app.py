"""FastAPI application: REST endpoints, live and terminal WebSockets, and the built interface."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, TypeVar

import alfred
from alfred.bootstrap import AlfredServices
from alfred.config.loader import discover_config
from alfred.domain.constants import RunStatus
from alfred.domain.models import AgentRun
from fastapi import FastAPI, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import alfred_ui
from alfred_ui import actions, artifacts, configedit, doctor, gitinfo, schemas
from alfred_ui.live import LiveHub
from alfred_ui.pipelines import PipelineRunner
from alfred_ui.reporting import build_reports
from alfred_ui.security import AccessGuard, TokenPolicy
from alfred_ui.snapshot import build_task_detail, describe_session
from alfred_ui.terminal import TerminalBridge
from alfred_ui.tmux_inspector import TmuxInspector, TmuxUnavailableError
from alfred_ui.transcripts import TranscriptStore
from alfred_ui.updater import Updater
from alfred_ui.workspace import NoWorkspaceError, WorkspaceContext

T = TypeVar("T")
DEFAULT_STATIC = Path(__file__).parent / "static"


def create_app(
    context: WorkspaceContext,
    *,
    policy: TokenPolicy | None = None,
    allowed_hosts: tuple[str, ...] = (),
    allowed_origins: tuple[str, ...] = (),
    static_directory: Path | None = DEFAULT_STATIC,
    inspector: TmuxInspector | None = None,
    hub: LiveHub | None = None,
    updater: Updater | None = None,
    runner: PipelineRunner | None = None,
) -> FastAPI:
    """Create the UI application for one workspace context."""
    tokens = policy or TokenPolicy(token=None)
    server = updater or Updater(static_directory=static_directory)
    tmux = inspector or TmuxInspector()
    live = hub or LiveHub(context, tmux)
    terminals = TerminalBridge(tmux)
    pipelines = runner or PipelineRunner()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await live.start()
        try:
            yield
        finally:
            terminals.close_all()
            await live.stop()

    app = FastAPI(
        title="Alfred UI",
        version=alfred_ui.__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.context = context
    app.state.hub = live
    app.state.inspector = tmux
    app.state.terminals = terminals
    _register_errors(app)

    def services() -> AlfredServices:
        return context.services()

    def mutate(operation: Callable[[AlfredServices], T]) -> T:
        with context.mutation_lock:
            try:
                return operation(context.services())
            finally:
                live.poke()

    # Health and sign-in -----------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "versions": _versions()}

    # Server: offline rebuild and self-restart ------------------------------------------------

    @app.get("/api/server")
    def server_status() -> dict[str, Any]:
        return server.describe()

    @app.post("/api/server/rebuild")
    def rebuild_frontend() -> dict[str, Any]:
        return server.rebuild()

    @app.post("/api/server/restart")
    def restart_server() -> dict[str, Any]:
        return server.request_restart()

    @app.get("/api/auth/status")
    def auth_status(request: Request) -> dict[str, Any]:
        return {
            "required": bool(tokens.token),
            "authenticated": tokens.authorized(request.headers, dict(request.query_params)),
        }

    @app.post("/api/auth/login")
    def login(body: schemas.LoginBody) -> Response:
        if not tokens.valid(body.token.strip()):
            return JSONResponse({"error": "That access token is not valid"}, status_code=401)
        response = JSONResponse({"ok": True})
        response.headers["set-cookie"] = tokens.cookie_header(body.token.strip())
        return response

    # Read model -------------------------------------------------------------------------------

    @app.get("/api/snapshot")
    def snapshot() -> dict[str, Any]:
        return live.current_snapshot()

    @app.get("/api/tasks/{number}")
    def task_detail(number: int) -> dict[str, Any]:
        return build_task_detail(
            services(),
            number,
            sessions=tmux.sessions(),
            worktrees=live.worktree_cache,
        )

    @app.get("/api/tasks/{number}/worktrees")
    def task_worktrees(number: int, diff: bool = False) -> dict[str, Any]:
        current = services()
        statuses = current.worktrees.statuses(number)
        return {
            "worktrees": gitinfo.worktree_details(current.config, statuses, include_diff=diff),
            "root": str(current.config.runtime.worktree_directory / f"task-{number}"),
        }

    @app.get("/api/runs")
    def runs(status: str = "") -> dict[str, Any]:
        selected = services().runs.list()
        if status:
            wanted = RunStatus(status)
            selected = tuple(run for run in selected if run.run_status == wanted)
        return {"runs": [_run(run) for run in selected]}

    @app.get("/api/events")
    def events(
        task: int | None = None,
        before: int | None = None,
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        records = [
            {"index": index, **record} for index, record in enumerate(services().store.events())
        ]
        if task is not None:
            records = [record for record in records if record.get("task_number") == task]
        if before is not None:
            records = [record for record in records if record["index"] < before]
        return {"events": records[-limit:], "total": len(records)}

    @app.get("/api/sessions")
    def sessions() -> dict[str, Any]:
        current = services()
        prefix = current.config.runtime.session_prefix
        by_session = {run.session_name: run for run in current.runs.list() if run.session_name}
        return {
            "prefix": prefix,
            "sessions": [
                describe_session(item, prefix, by_session.get(item.name))
                for item in tmux.sessions()
                if item.name.startswith(f"{prefix}-")
            ],
        }

    @app.get("/api/sessions/{name}/capture")
    def capture(name: str, history: int = Query(default=0, ge=0, le=100000)) -> dict[str, Any]:
        actions.owned_session(services(), name)
        if not tmux.exists(name):
            raise ValueError(f"Session {name} is not running")
        pane = tmux.pane(name)
        return {
            "session": name,
            "content": tmux.capture(name, history=history, ansi=True),
            "pane": pane.to_dict() if pane else None,
        }

    @app.get("/api/prompts/{name}")
    def prompt(name: str) -> dict[str, Any]:
        return artifacts.read_prompt(services().config, name)

    @app.get("/api/transcripts")
    def transcripts(task: int | None = None) -> dict[str, Any]:
        return {"transcripts": TranscriptStore.for_config(services().config).list(task)}

    @app.get("/api/transcripts/read")
    def transcript(path: str) -> dict[str, Any]:
        return TranscriptStore.for_config(services().config).read(path)

    @app.get("/api/knowledge")
    def knowledge(category: str | None = None, task: int | None = None) -> dict[str, Any]:
        config = services().config
        return {
            "directory": str(config.knowledge.directory),
            "required_completion_entries": config.knowledge.required_completion_entries,
            "entries": artifacts.knowledge_entries(
                config, category=category or None, task_number=task
            ),
        }

    @app.get("/api/knowledge/entry")
    def knowledge_entry(path: str) -> dict[str, Any]:
        return artifacts.read_knowledge(services().config, path)

    @app.get("/api/completions")
    def completions(task: int | None = None) -> dict[str, Any]:
        return artifacts.completion_files(services().config, task)

    @app.get("/api/reports")
    def reports() -> dict[str, Any]:
        current = services()
        records = current.store.events()
        return build_reports(current.tasks.list(), current.runs.list(), records)

    @app.get("/api/tracker")
    def tracker(task: int | None = None) -> dict[str, Any]:
        current = services()
        return {
            **artifacts.tracker_files(current.config),
            "issues": list(current.sync.validate(task)),
        }

    @app.get("/api/tracker/daily/{name}")
    def daily_note(name: str) -> dict[str, Any]:
        return artifacts.read_daily_note(services().config, name)

    @app.get("/api/state/{name}")
    def state_document(name: str) -> dict[str, Any]:
        return artifacts.state_document(context.config(), name)

    @app.get("/api/doctor")
    def health_checks() -> dict[str, Any]:
        return {"checks": doctor.run_checks(context, tmux)}

    # Workspace and configuration ---------------------------------------------------------------

    @app.get("/api/workspace")
    def workspace() -> dict[str, Any]:
        path = context.config_path
        return {
            "config_path": str(path or ""),
            "discovery_error": context.discovery_error,
            "cwd": str(Path.cwd()),
            "home": str(Path.home()),
        }

    @app.post("/api/workspace/open")
    def open_workspace(body: schemas.OpenWorkspaceBody) -> dict[str, Any]:
        target = Path(body.path).expanduser()
        if target.is_dir():
            candidate = target / ".alfred" / "config.toml"
            target = candidate if candidate.is_file() else discover_config(target, environment={})
        with context.mutation_lock:
            opened = context.open(target, validate=body.validate_config)
        live.poke()
        return {"ok": True, "message": f"Opened {opened}", "config_path": str(opened)}

    @app.post("/api/workspace/init")
    def init_workspace(body: schemas.InitBody) -> dict[str, Any]:
        with context.mutation_lock:
            path = actions.initialize(body)
            context.open(path)
        live.poke()
        return {
            "ok": True,
            "message": f"Initialized Alfred workspace: {path}",
            "config_path": str(path),
        }

    @app.get("/api/config")
    def config_text() -> dict[str, Any]:
        path = context.require_path()
        text = configedit.read_config_text(path)
        validation = configedit.validate_config_text(path, text)
        return {
            "path": str(path),
            "text": text,
            "validation": validation,
            "backup_exists": path.with_name(f"{path.name}.bak").is_file(),
            "presets": configedit.AGENT_PRESETS,
        }

    @app.post("/api/config/validate")
    def validate_config(body: schemas.ConfigTextBody) -> dict[str, Any]:
        return configedit.validate_config_text(context.require_path(), body.text)

    @app.put("/api/config")
    def save_config(body: schemas.ConfigTextBody) -> dict[str, Any]:
        with context.mutation_lock:
            result = configedit.save_config_text(context.require_path(), body.text)
        live.poke()
        return {"ok": True, "message": "Configuration saved", **result}

    @app.post("/api/config/snippets/repository")
    def repository_snippet(body: schemas.RepositorySnippetBody) -> dict[str, str]:
        return {
            "text": configedit.repository_block(
                body.name.strip(),
                body.path.strip(),
                default_branch=body.default_branch.strip() or "main",
                remote=body.remote.strip() or "origin",
                selected_by_default=body.selected_by_default,
            )
        }

    @app.post("/api/config/snippets/agent")
    def agent_snippet(body: schemas.AgentSnippetBody) -> dict[str, str]:
        commands = {
            "direct": [item for item in body.direct if item],
            "plan": [item for item in body.plan if item],
            "execution": [item for item in body.execution if item],
        }
        alias = body.alias.strip()
        return {
            "text": configedit.agent_block(alias, body.runtime_target.strip() or alias, commands)
        }

    @app.post("/api/doctor/tmux-path")
    def fix_tmux_path() -> dict[str, Any]:
        message = doctor.fix_tmux_path(tmux)
        live.poke()
        return {"ok": True, "message": message}

    @app.post("/api/migrate")
    def migrate(body: schemas.MigrateBody) -> dict[str, Any]:
        return mutate(lambda current: actions.migrate(current, body))

    # Tasks ------------------------------------------------------------------------------------

    @app.post("/api/tasks")
    def create_task(body: schemas.TaskCreateBody) -> dict[str, Any]:
        return mutate(lambda current: actions.create_task(current, body))

    @app.patch("/api/tasks/{number}")
    def update_task(number: int, body: schemas.TaskUpdateBody) -> dict[str, Any]:
        return mutate(lambda current: actions.update_task(current, number, body))

    @app.post("/api/tasks/{number}/start")
    def start_task(number: int, body: schemas.ActorBody) -> dict[str, Any]:
        return mutate(lambda current: actions.start_task(current, number, body))

    @app.post("/api/tasks/{number}/progress")
    def progress_task(number: int, body: schemas.RequiredNoteBody) -> dict[str, Any]:
        return mutate(lambda current: actions.progress_task(current, number, body))

    @app.post("/api/tasks/{number}/block")
    def block_task(number: int, body: schemas.BlockBody) -> dict[str, Any]:
        return mutate(lambda current: actions.block_task(current, number, body))

    @app.post("/api/tasks/{number}/unblock")
    def unblock_task(number: int, body: schemas.NoteBody) -> dict[str, Any]:
        return mutate(lambda current: actions.unblock_task(current, number, body))

    @app.post("/api/tasks/{number}/hold")
    def hold_task(number: int, body: schemas.NoteBody) -> dict[str, Any]:
        return mutate(lambda current: actions.hold_task(current, number, body))

    @app.post("/api/tasks/{number}/review")
    def review_task(number: int, body: schemas.ReviewBody) -> dict[str, Any]:
        return mutate(lambda current: actions.review_task(current, number, body))

    @app.post("/api/tasks/{number}/merge")
    def merge_task(number: int, body: schemas.MergeBody) -> dict[str, Any]:
        return mutate(lambda current: actions.merge_task(current, number, body))

    @app.post("/api/tasks/{number}/deploy")
    def deploy_task(number: int, body: schemas.DeployBody) -> dict[str, Any]:
        return mutate(lambda current: actions.deploy_task(current, number, body))

    @app.post("/api/tasks/{number}/archive")
    def archive_task(number: int, body: schemas.NoteBody) -> dict[str, Any]:
        return mutate(lambda current: actions.archive_task(current, number, body))

    @app.post("/api/tasks/{number}/cancel")
    def cancel_task(number: int, body: schemas.CancelBody) -> dict[str, Any]:
        return mutate(lambda current: actions.cancel_task(current, tmux, number, body))

    @app.post("/api/tasks/{number}/consolidate")
    def consolidate_task(number: int, body: schemas.NoteBody) -> dict[str, Any]:
        return mutate(lambda current: actions.consolidate_task(current, number, body))

    @app.post("/api/tasks/{number}/assign")
    def assign_agent(number: int, body: schemas.AssignBody) -> dict[str, Any]:
        return mutate(lambda current: actions.assign_agent(current, number, body))

    @app.post("/api/tasks/{number}/reassign")
    def reassign_agent(number: int, body: schemas.ReassignBody) -> dict[str, Any]:
        return mutate(lambda current: actions.reassign_agent(current, number, body))

    # Runs -------------------------------------------------------------------------------------

    @app.post("/api/runs/trigger")
    def trigger(body: schemas.TriggerBody) -> dict[str, Any]:
        return mutate(lambda current: actions.trigger_runs(current, body))

    @app.post("/api/tasks/{number}/run/stop")
    def stop_run(number: int, body: schemas.StopBody) -> dict[str, Any]:
        return mutate(lambda current: actions.stop_run(current, tmux, number, body))

    @app.post("/api/tasks/{number}/run/continue")
    def continue_run(number: int, body: schemas.ContinueBody) -> dict[str, Any]:
        return mutate(lambda current: actions.continue_run(current, tmux, number, body))

    @app.post("/api/tasks/{number}/run/event")
    def record_event(number: int, body: schemas.EventBody) -> dict[str, Any]:
        return mutate(lambda current: actions.record_event(current, number, body))

    @app.post("/api/tasks/{number}/run/complete")
    def complete_run(number: int, body: schemas.CompleteBody) -> dict[str, Any]:
        return mutate(lambda current: actions.complete_run(current, number, body))

    @app.post("/api/tasks/{number}/run/reopen")
    def reopen_run(number: int, body: schemas.ActorBody) -> dict[str, Any]:
        return mutate(lambda current: actions.reopen_run(current, number, body))

    # Worktrees --------------------------------------------------------------------------------

    @app.post("/api/tasks/{number}/worktrees")
    def create_worktrees(number: int, body: schemas.WorktreeCreateBody) -> dict[str, Any]:
        return mutate(lambda current: actions.create_worktrees(current, number, body))

    @app.post("/api/tasks/{number}/worktrees/commit")
    def commit_worktrees(number: int, body: schemas.CommitBody) -> dict[str, Any]:
        return mutate(lambda current: actions.commit_worktrees(current, number, body))

    @app.post("/api/tasks/{number}/worktrees/push")
    def push_worktrees(number: int, body: schemas.PushBody) -> dict[str, Any]:
        return mutate(lambda current: actions.push_worktrees(current, number, body))

    @app.post("/api/tasks/{number}/worktrees/remove")
    def remove_worktrees(number: int, body: schemas.RemoveWorktreesBody) -> dict[str, Any]:
        return mutate(lambda current: actions.remove_worktrees(current, number, body))

    # Review -----------------------------------------------------------------------------------

    @app.get("/api/tasks/{number}/review")
    def review(number: int) -> dict[str, Any]:
        return actions.review_document(services(), number)

    @app.post("/api/tasks/{number}/review/comments")
    def add_review_comment(number: int, body: schemas.ReviewCommentBody) -> dict[str, Any]:
        return mutate(lambda current: actions.add_review_comment(current, number, body))

    @app.post("/api/tasks/{number}/review/comments/{comment_id}")
    def update_review_comment(
        number: int, comment_id: str, body: schemas.ReviewCommentUpdateBody
    ) -> dict[str, Any]:
        return mutate(
            lambda current: actions.update_review_comment(current, number, comment_id, body)
        )

    @app.post("/api/tasks/{number}/review/comments/{comment_id}/delete")
    def delete_review_comment(number: int, comment_id: str) -> dict[str, Any]:
        return mutate(lambda current: actions.delete_review_comment(current, number, comment_id))

    @app.post("/api/tasks/{number}/review/approve")
    def approve_review(number: int, body: schemas.ReviewDecisionBody) -> dict[str, Any]:
        return mutate(lambda current: actions.approve_review(current, number, body))

    @app.post("/api/tasks/{number}/review/request-changes")
    def request_review_changes(number: int, body: schemas.ReviewDecisionBody) -> dict[str, Any]:
        return mutate(lambda current: actions.request_review_changes(current, tmux, number, body))

    # Pipelines --------------------------------------------------------------------------------

    @app.get("/api/pipelines")
    def pipelines_config() -> dict[str, Any]:
        return actions.pipelines_config(services())

    @app.post("/api/pipelines/validate")
    def validate_pipelines(body: schemas.PipelinesConfigBody) -> dict[str, Any]:
        return actions.validate_pipelines(services(), body)

    @app.put("/api/pipelines")
    def save_pipelines(body: schemas.PipelinesConfigBody) -> dict[str, Any]:
        return mutate(lambda current: actions.save_pipelines(current, body))

    @app.get("/api/tasks/{number}/pipelines")
    def task_pipelines(number: int) -> dict[str, Any]:
        return actions.task_pipelines(services(), pipelines, number)

    @app.post("/api/tasks/{number}/pipelines")
    def start_pipeline(number: int, body: schemas.ActorBody) -> dict[str, Any]:
        return mutate(
            lambda current: actions.start_pipeline(current, pipelines, number, body, live.poke)
        )

    @app.post("/api/tasks/{number}/pipelines/{run_id}/cancel")
    def cancel_pipeline(number: int, run_id: str) -> dict[str, Any]:
        run = pipelines.cancel(services().config, number, run_id)
        return {"ok": True, "message": f"Cancelling pipeline {run_id}", "run": run}

    @app.get("/api/tasks/{number}/pipelines/{run_id}/steps/{index}/log")
    def pipeline_log(
        number: int, run_id: str, index: int, offset: int = Query(default=0, ge=0)
    ) -> dict[str, Any]:
        return pipelines.log(services().config, number, run_id, index, offset)

    # Tracker, knowledge, coordinator, learner, notifications ------------------------------------

    @app.post("/api/sync/apply")
    def sync_apply(body: schemas.SyncApplyBody) -> dict[str, Any]:
        return mutate(lambda current: actions.sync_apply(current, body))

    @app.post("/api/knowledge")
    def add_knowledge(body: schemas.KnowledgeBody) -> dict[str, Any]:
        return mutate(lambda current: actions.add_knowledge(current, body))

    @app.get("/api/skills")
    def skills() -> dict[str, Any]:
        return actions.list_skills(services())

    @app.put("/api/skills/{phase}")
    def set_skill(phase: schemas.SkillPhase, body: schemas.SkillBody) -> dict[str, Any]:
        return mutate(lambda current: actions.set_skill(current, phase, body))

    @app.post("/api/skills/{phase}/remove")
    def remove_skill(phase: schemas.SkillPhase) -> dict[str, Any]:
        return mutate(lambda current: actions.remove_skill(current, phase))

    @app.post("/api/coordinator/start")
    def coordinator_start() -> dict[str, Any]:
        return mutate(actions.coordinator_start)

    @app.post("/api/coordinator/stop")
    def coordinator_stop() -> dict[str, Any]:
        return mutate(actions.coordinator_stop)

    @app.post("/api/coordinator/once")
    def coordinator_once() -> dict[str, Any]:
        return mutate(actions.coordinator_once)

    @app.post("/api/learner/start")
    def learner_start(body: schemas.LearnerStartBody) -> dict[str, Any]:
        return mutate(lambda current: actions.learner_start(current, body))

    @app.post("/api/learner/stop")
    def learner_stop() -> dict[str, Any]:
        return mutate(actions.learner_stop)

    @app.post("/api/notifications/ack")
    def acknowledge(body: schemas.NotificationAckBody) -> dict[str, Any]:
        return mutate(lambda current: actions.acknowledge_notifications(current, body))

    @app.post("/api/notifications/clear")
    def clear_notifications() -> dict[str, Any]:
        return mutate(actions.clear_notifications)

    # Sessions ---------------------------------------------------------------------------------

    @app.post("/api/sessions/{name}/send")
    def send_text(name: str, body: schemas.SendTextBody) -> dict[str, Any]:
        return mutate(lambda current: actions.send_text(current, tmux, name, body))

    @app.post("/api/sessions/{name}/keys")
    def send_keys(name: str, body: schemas.SendKeysBody) -> dict[str, Any]:
        return mutate(lambda current: actions.send_keys(current, tmux, name, body))

    @app.post("/api/sessions/{name}/kill")
    def kill_session(name: str, body: schemas.KillSessionBody) -> dict[str, Any]:
        return mutate(lambda current: actions.kill_session(current, tmux, name, body))

    @app.post("/api/sessions/{name}/capture")
    def capture_session(name: str, body: schemas.CaptureBody) -> dict[str, Any]:
        return mutate(lambda current: actions.capture_session(current, tmux, name, body))

    # WebSockets -------------------------------------------------------------------------------

    @app.websocket("/api/live")
    async def live_updates(websocket: WebSocket) -> None:
        await live.serve(websocket)

    @app.websocket("/api/terminal/{name}")
    async def terminal(
        websocket: WebSocket,
        name: str,
        mode: str = "interactive",
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        try:
            current = await _threaded(services)
            actions.owned_session(current, name)
        except (NoWorkspaceError, PermissionError, ValueError, KeyError, OSError) as exc:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=4003)
            return
        await terminals.serve(websocket, name, readonly=mode == "readonly", cols=cols, rows=rows)

    app.add_middleware(
        AccessGuard,
        policy=tokens,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    _register_frontend(app, static_directory)
    return app


async def _threaded(function: Callable[[], T]) -> T:
    return await asyncio.to_thread(function)


def _versions() -> dict[str, str]:
    return {"alfred": alfred.__version__, "ui": alfred_ui.__version__}


def _run(run: AgentRun) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, str) else value for key, value in run.to_dict().items()
    }


def _register_errors(app: FastAPI) -> None:
    def respond(status: int, message: str, kind: str) -> JSONResponse:
        return JSONResponse({"error": message, "kind": kind}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            message = str(error.get("msg", "invalid"))
            problems.append(f"{location}: {message}" if location else message)
        return respond(400, "; ".join(problems) or "Invalid request", "validation")

    @app.exception_handler(NoWorkspaceError)
    async def no_workspace(_: Request, exc: NoWorkspaceError) -> JSONResponse:
        return respond(409, str(exc), "no_workspace")

    @app.exception_handler(PermissionError)
    async def permission(_: Request, exc: PermissionError) -> JSONResponse:
        return respond(403, str(exc), "permission")

    @app.exception_handler(FileExistsError)
    async def exists(_: Request, exc: FileExistsError) -> JSONResponse:
        return respond(409, str(exc), "exists")

    @app.exception_handler(KeyError)
    async def key_error(_: Request, exc: KeyError) -> JSONResponse:
        return respond(400, str(exc.args[0] if exc.args else exc), "key")

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return respond(400, str(exc), type(exc).__name__)

    @app.exception_handler(TmuxUnavailableError)
    async def tmux_unavailable(_: Request, exc: TmuxUnavailableError) -> JSONResponse:
        return respond(503, str(exc), "tmux")

    @app.exception_handler(RuntimeError)
    async def runtime_error(_: Request, exc: RuntimeError) -> JSONResponse:
        return respond(400, str(exc), type(exc).__name__)

    @app.exception_handler(OSError)
    async def os_error(_: Request, exc: OSError) -> JSONResponse:
        return respond(400, str(exc), type(exc).__name__)


def _register_frontend(app: FastAPI, static_directory: Path | None) -> None:
    index = static_directory / "index.html" if static_directory else None
    if static_directory is None or index is None or not index.is_file():

        @app.get("/", include_in_schema=False)
        def missing_frontend() -> HTMLResponse:
            return HTMLResponse(
                "<!doctype html><title>Alfred UI</title><p>The Alfred UI frontend is not built. "
                "Run <code>npm install && npm run build</code> in <code>ui/web</code>, then "
                "restart <code>alfred-ui</code>. The API is available under /api.</p>",
                status_code=503,
            )

        return
    root = static_directory.resolve()
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> Response:
        if path.startswith("api/") or path == "api":
            return JSONResponse({"error": f"No API route /{path}"}, status_code=404)
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html", headers={"cache-control": "no-cache"})

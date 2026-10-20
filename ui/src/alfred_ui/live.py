"""Watch Alfred's files and tmux sessions, and stream snapshots to connected browsers.

Agents change state by running ``alfred`` in their own sessions, so the hub cannot rely on the
UI's own actions: it fingerprints every state, completion, knowledge, tracker, prompt, and
transcript file several times a second, polls tmux and worktree status on slower cadences, and
pushes a fresh snapshot whenever anything changed.
"""

import asyncio
import contextlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from alfred.config.loader import load_config
from alfred.config.models import AlfredConfig
from starlette.websockets import WebSocket, WebSocketDisconnect

from alfred_ui import gitinfo
from alfred_ui.snapshot import build_snapshot
from alfred_ui.tmux_inspector import SessionInfo, TmuxInspector
from alfred_ui.transcripts import TranscriptStore
from alfred_ui.workspace import WorkspaceContext

LOG = logging.getLogger(__name__)
AUTO_TRANSCRIPTS = {
    "AGENT_PLAN_COMPLETED": "plan",
    "RUN_COMPLETED": "completed",
    "RUN_FAILED": "failed",
    "RUN_BLOCKED": "blocked",
}


class LiveHub:
    """Detect changes and broadcast snapshots over WebSockets."""

    def __init__(
        self,
        context: WorkspaceContext,
        inspector: TmuxInspector,
        *,
        poll_interval: float = 0.4,
        session_interval: float = 1.0,
        worktree_interval: float = 4.0,
        heartbeat_interval: float = 15.0,
        auto_transcripts: bool = True,
    ) -> None:
        self.context = context
        self.inspector = inspector
        self.poll_interval = poll_interval
        self.session_interval = session_interval
        self.worktree_interval = worktree_interval
        self.heartbeat_interval = heartbeat_interval
        self.auto_transcripts = auto_transcripts
        self.clients: set[WebSocket] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._payload: str | None = None
        self._payload_generation = -1
        self._generation = -1
        self._fingerprint: tuple[Any, ...] | None = None
        self._config: tuple[tuple[int, int] | None, AlfredConfig] | None = None
        self._sessions: tuple[SessionInfo, ...] = ()
        self._session_signature: tuple[Any, ...] | None = None
        self._activity: dict[str, int] = {}
        self._sessions_at = 0.0
        self._worktrees: dict[int, list[dict[str, Any]]] = {}
        self._worktrees_at = 0.0
        self._event_count: int | None = None
        self.ticks = 0

    async def start(self) -> None:
        """Start the background watcher on the running event loop."""
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="alfred-ui-live")

    async def stop(self) -> None:
        """Stop watching and close every client."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for websocket in list(self.clients):
            with contextlib.suppress(Exception):
                await websocket.close()
        self.clients.clear()

    def poke(self) -> None:
        """Ask for an immediate check; safe to call from any thread."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(wake.set)

    async def serve(self, websocket: WebSocket) -> None:
        """Send the current snapshot, then keep the client subscribed until it leaves."""
        await websocket.accept()
        try:
            await websocket.send_text(await self._current_payload())
            self.clients.add(websocket)
            while True:
                message = await websocket.receive_text()
                with contextlib.suppress(ValueError):
                    request = json.loads(message)
                    if isinstance(request, dict) and request.get("type") == "refresh":
                        self.poke()
        except WebSocketDisconnect:
            pass
        finally:
            self.clients.discard(websocket)

    @property
    def worktree_cache(self) -> dict[int, list[dict[str, Any]]]:
        """Return the latest worktree summaries gathered by the watcher."""
        return dict(self._worktrees)

    def current_snapshot(self) -> dict[str, Any]:
        """Build a snapshot synchronously, reusing the watcher's session and worktree caches."""
        with self._lock:
            return build_snapshot(
                self.context,
                self.inspector,
                sessions=self._sessions if self._sessions_at else None,
                worktrees=self._worktrees,
            )

    async def _current_payload(self) -> str:
        for _ in range(60):
            if self._payload is not None and self._payload_generation == self.context.generation:
                return self._payload
            self.poke()
            await asyncio.sleep(0.05)
        snapshot = await asyncio.to_thread(self.current_snapshot)
        return json.dumps({"type": "snapshot", "data": snapshot}, default=str)

    async def _run(self) -> None:
        assert self._wake is not None
        last_heartbeat = time.monotonic()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            forced = self._wake.is_set()
            self._wake.clear()
            try:
                messages = await asyncio.to_thread(self._tick, forced)
            except Exception:  # pragma: no cover - defensive: keep watching after surprises
                LOG.exception("Live update failed")
                messages = []
            now = time.monotonic()
            if not messages and now - last_heartbeat >= self.heartbeat_interval:
                messages = [json.dumps({"type": "heartbeat", "server_time": time.time()})]
            if messages:
                last_heartbeat = now
                await self._broadcast(messages)

    def _tick(self, forced: bool) -> list[str]:
        with self._lock:
            return self._tick_locked(forced)

    def _tick_locked(self, forced: bool) -> list[str]:
        self.ticks += 1
        generation = self.context.generation
        if generation != self._generation:
            self._generation = generation
            self._fingerprint = None
            self._config = None
            self._sessions_at = 0.0
            self._worktrees = {}
            self._worktrees_at = 0.0
            self._event_count = None
            forced = True
        config = self._load_config()
        fingerprint = _fingerprint(self.context.config_path, config)
        changed = forced or fingerprint != self._fingerprint
        self._fingerprint = fingerprint
        now = time.monotonic()

        sessions_changed = False
        activity_changed = False
        if changed or now - self._sessions_at >= self.session_interval:
            self._sessions = self.inspector.sessions()
            self._sessions_at = now
            signature = _session_signature(self._sessions)
            activity = {item.name: item.activity for item in self._sessions}
            sessions_changed = signature != self._session_signature
            activity_changed = activity != self._activity
            self._session_signature = signature
            self._activity = activity

        worktrees_changed = False
        if config is not None and (changed or now - self._worktrees_at >= self.worktree_interval):
            worktrees = gitinfo.summaries(config, gitinfo.tasks_with_worktrees(config))
            worktrees_changed = worktrees != self._worktrees
            self._worktrees = worktrees
            self._worktrees_at = now

        if changed or sessions_changed or worktrees_changed:
            snapshot = build_snapshot(
                self.context,
                self.inspector,
                sessions=self._sessions,
                worktrees=self._worktrees,
            )
            if config is not None:
                self._capture_transcripts(config, snapshot)
            payload = json.dumps({"type": "snapshot", "data": snapshot}, default=str)
            self._payload = payload
            self._payload_generation = generation
            return [payload]
        if activity_changed:
            return [
                json.dumps({"type": "activity", "data": self._activity, "server_time": time.time()})
            ]
        return []

    def _load_config(self) -> AlfredConfig | None:
        path = self.context.config_path
        if path is None:
            self._config = None
            return None
        stamp = _stat(path)
        if self._config is not None and self._config[0] == stamp:
            return self._config[1]
        try:
            config = load_config(path)
        except (ValueError, OSError, KeyError):
            self._config = None
            return None
        self._config = (stamp, config)
        return config

    def _capture_transcripts(self, config: AlfredConfig, snapshot: dict[str, Any]) -> None:
        count = snapshot.get("event_count")
        if not isinstance(count, int):
            return
        previous = self._event_count
        self._event_count = count
        if previous is None or count <= previous or not self.auto_transcripts:
            return
        tasks = {task["task_number"]: task for task in snapshot.get("tasks", [])}
        store = TranscriptStore.for_config(config)
        for event in snapshot.get("events", []):
            reason = AUTO_TRANSCRIPTS.get(str(event.get("event_type")))
            if reason is None or event.get("index", -1) < previous:
                continue
            number = event.get("task_number")
            session = (tasks.get(number) or {}).get("derived", {}).get("session") or {}
            name = session.get("name")
            if not name or not session.get("alive"):
                continue
            try:
                store.capture(self.inspector, name, task_number=number, reason=reason)
            except (OSError, RuntimeError, ValueError):
                LOG.warning("Could not save a %s transcript for %s", reason, name)

    async def _broadcast(self, messages: list[str]) -> None:
        async def deliver(websocket: WebSocket) -> WebSocket | None:
            try:
                for message in messages:
                    await asyncio.wait_for(websocket.send_text(message), timeout=5)
            except Exception:
                return websocket
            return None

        failed = await asyncio.gather(*(deliver(item) for item in list(self.clients)))
        for websocket in failed:
            if websocket is not None:
                self.clients.discard(websocket)


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _listing(directory: Path, pattern: str = "*") -> tuple[tuple[str, int], ...]:
    try:
        entries = ((item.name, item.stat().st_mtime_ns) for item in directory.glob(pattern))
        return tuple(sorted(entries))
    except OSError:
        return ()


def _fingerprint(config_path: Path | None, config: AlfredConfig | None) -> tuple[Any, ...]:
    if config_path is None:
        return ("no-workspace",)
    if config is None:
        return ("invalid-config", _stat(config_path))
    state = config.runtime.state_directory
    temp = config.runtime.temp_directory
    tracker = config.trackers.markdown
    knowledge = config.knowledge.directory
    transcripts = temp / "ui" / "transcripts"
    return (
        _stat(config_path),
        tuple(_stat(state / f"{name}.json") for name in _STATE_DOCUMENTS),
        _stat(state / "learner.active"),
        tuple(_listing(temp / "completions" / bucket) for bucket in _COMPLETION_BUCKETS),
        _listing(temp / "prompts"),
        tuple(_listing(path) for path in sorted(transcripts.glob("*")) if path.is_dir())
        if transcripts.is_dir()
        else (),
        tuple(_listing(knowledge / category) for category in _KNOWLEDGE_CATEGORIES),
        _stat(tracker.canonical) if tracker.canonical else None,
        _stat(tracker.agents) if tracker.agents else None,
        _listing(tracker.daily_notes) if tracker.daily_notes else (),
        _listing(config.runtime.worktree_directory, "task-*"),
    )


def _session_signature(sessions: tuple[SessionInfo, ...]) -> tuple[Any, ...]:
    signature: list[Any] = []
    for session in sessions:
        pane = session.active_pane
        signature.append(
            (
                session.name,
                session.attached,
                session.windows,
                (
                    pane.dead,
                    pane.current_command,
                    pane.pid,
                    pane.width,
                    pane.height,
                    pane.alternate_screen,
                )
                if pane
                else None,
            )
        )
    return tuple(signature)


_STATE_DOCUMENTS = ("tasks", "runs", "queue", "notifications", "events")
_COMPLETION_BUCKETS = ("pending", "processed", "invalid")
_KNOWLEDGE_CATEGORIES = ("patterns", "decisions", "entities", "issues", "conventions")

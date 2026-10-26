"""Local pipelines: configured check steps run in a task's worktrees, with live logs.

Steps are declared in ``pipelines.toml`` next to ``config.toml``. Each step is an argument array
run in one repository's task worktree, never through a shell. Steps of one repository run in
order and stop at the first failure; repositories are independent. Runs, their state, and one
log per step live under the private temp directory.
"""

import json
import os
import signal
import subprocess
import threading
import time
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alfred.config.models import AlfredConfig
from alfred.utils.files import atomic_write_text

PIPELINES_VERSION = 1
PIPELINES_FILENAME = "pipelines.toml"
DEFAULT_TIMEOUT_MINUTES = 30
MAX_STEPS = 50
MAX_LOG_CHUNK = 256_000
MAX_LISTED_RUNS = 20
FINISHED = {"passed", "failed", "cancelled", "interrupted"}
EXAMPLE = """\
version = 1

# Each step runs in one repository's task worktree. `command` is an argument array, not a
# shell string. A repository's steps stop at its first failure.
#
# [[steps]]
# repository = "app"
# name = "Unit tests"
# command = ["./gradlew", "test"]
# timeout_minutes = 30
"""


@dataclass(frozen=True, slots=True)
class PipelineStep:
    """One configured check."""

    repository: str
    name: str
    command: tuple[str, ...]
    timeout_minutes: int


def pipelines_path(config: AlfredConfig) -> Path:
    """Return where a workspace's pipeline steps are declared."""
    return config.config_path.parent / PIPELINES_FILENAME


def parse_steps(text: str, repositories: set[str]) -> tuple[PipelineStep, ...]:
    """Parse and strictly validate ``pipelines.toml``; unknown keys are rejected."""
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in {PIPELINES_FILENAME}: {exc}") from exc
    unknown = set(payload) - {"version", "steps"}
    if unknown:
        raise ValueError(f"Unknown keys in {PIPELINES_FILENAME}: {', '.join(sorted(unknown))}")
    if payload.get("version") != PIPELINES_VERSION:
        raise ValueError(f"{PIPELINES_FILENAME} must declare version = {PIPELINES_VERSION}")
    tables = payload.get("steps", [])
    if not isinstance(tables, list) or len(tables) > MAX_STEPS:
        raise ValueError(f"steps must be a list of at most {MAX_STEPS} [[steps]] tables")
    steps: list[PipelineStep] = []
    for index, table in enumerate(tables, start=1):
        where = f"steps[{index}]"
        if not isinstance(table, dict):
            raise ValueError(f"{where} must be a table")
        extra = set(table) - {"repository", "name", "command", "timeout_minutes"}
        if extra:
            raise ValueError(f"Unknown keys in {where}: {', '.join(sorted(extra))}")
        repository = table.get("repository")
        if repository not in repositories:
            raise ValueError(f"{where}.repository must be a configured repository")
        name = table.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError(f"{where}.name must be 1 to 80 characters")
        command = table.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) for part in command)
            or not command[0].strip()
        ):
            raise ValueError(f"{where}.command must be a non-empty array of strings")
        timeout = table.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 720:
            raise ValueError(f"{where}.timeout_minutes must be 1 to 720")
        steps.append(PipelineStep(str(repository), name.strip(), tuple(command), timeout))
    return tuple(steps)


def load_steps(config: AlfredConfig) -> tuple[PipelineStep, ...]:
    """Return the workspace's steps; a missing file means none."""
    path = pipelines_path(config)
    if not path.is_file():
        return ()
    names = {repository.name for repository in config.workspace.repositories}
    return parse_steps(path.read_text(encoding="utf-8"), names)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PipelineRunner:
    """Start, follow, and cancel pipeline runs; one active run per task."""

    def __init__(self, *, poll_interval: float = 0.2) -> None:
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._active: dict[str, threading.Event] = {}

    @staticmethod
    def directory(config: AlfredConfig, task_number: int) -> Path:
        """Return where one task's runs are kept."""
        return config.runtime.temp_directory / "ui" / "pipelines" / f"task-{int(task_number)}"

    def start(
        self,
        config: AlfredConfig,
        task_number: int,
        worktrees: dict[str, Path],
        heads: dict[str, str],
        *,
        actor: str,
        on_change: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        """Start the configured steps for the repositories the task has worktrees in."""
        steps = [step for step in load_steps(config) if step.repository in worktrees]
        if not steps:
            raise ValueError(
                f"No pipeline steps for task {task_number}'s repositories; "
                f"declare them in {PIPELINES_FILENAME}"
            )
        with self._lock:
            if any(run["status"] == "running" for run in self.runs(config, task_number)):
                raise ValueError(f"Task {task_number} already has a running pipeline")
            run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
            folder = self.directory(config, task_number) / run_id
            folder.mkdir(parents=True)
            run: dict[str, Any] = {
                "version": PIPELINES_VERSION,
                "id": run_id,
                "task_number": task_number,
                "actor": actor,
                "status": "running",
                "started_at": _now(),
                "finished_at": None,
                "heads": {
                    name: heads.get(name, "") for name in sorted({s.repository for s in steps})
                },
                "steps": [
                    {
                        "index": index,
                        "repository": step.repository,
                        "name": step.name,
                        "command": list(step.command),
                        "timeout_minutes": step.timeout_minutes,
                        "status": "pending",
                        "started_at": None,
                        "finished_at": None,
                        "exit_code": None,
                        "log": f"step-{index:02d}.log",
                    }
                    for index, step in enumerate(steps, start=1)
                ],
            }
            _save(folder, run)
            cancel = threading.Event()
            self._active[run_id] = cancel
        thread = threading.Thread(
            target=self._execute,
            args=(folder, run, worktrees, cancel, on_change),
            name=f"alfred-pipeline-{run_id}",
            daemon=True,
        )
        thread.start()
        return run

    def runs(self, config: AlfredConfig, task_number: int) -> list[dict[str, Any]]:
        """Return a task's most recent runs, newest first."""
        root = self.directory(config, task_number)
        if not root.is_dir():
            return []
        folders = sorted((item for item in root.iterdir() if item.is_dir()), reverse=True)
        return [self._read(folder) for folder in folders[:MAX_LISTED_RUNS]]

    def run(self, config: AlfredConfig, task_number: int, run_id: str) -> dict[str, Any]:
        """Return one run."""
        return self._read(self._folder(config, task_number, run_id))

    def cancel(self, config: AlfredConfig, task_number: int, run_id: str) -> dict[str, Any]:
        """Stop a running pipeline; its current step is terminated."""
        run = self.run(config, task_number, run_id)
        event = self._active.get(run_id)
        if run["status"] != "running" or event is None:
            raise ValueError(f"Pipeline {run_id} is not running")
        event.set()
        return run

    def log(
        self, config: AlfredConfig, task_number: int, run_id: str, index: int, offset: int
    ) -> dict[str, Any]:
        """Return a step's log from ``offset``, at most one chunk at a time."""
        folder = self._folder(config, task_number, run_id)
        run = self._read(folder)
        step = next((item for item in run["steps"] if item["index"] == index), None)
        if step is None:
            raise KeyError(f"Pipeline {run_id} has no step {index}")
        path = folder / step["log"]
        if not path.is_file():
            return {"text": "", "offset": 0, "size": 0, "status": step["status"]}
        size = path.stat().st_size
        start = max(0, min(offset, size))
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(MAX_LOG_CHUNK)
        return {
            "text": data.decode("utf-8", errors="replace"),
            "offset": start + len(data),
            "size": size,
            "status": step["status"],
        }

    def _folder(self, config: AlfredConfig, task_number: int, run_id: str) -> Path:
        root = self.directory(config, task_number)
        folder = (root / run_id).resolve()
        if folder.parent != root.resolve() or not folder.is_dir():
            raise KeyError(f"Pipeline {run_id} not found")
        return folder

    def _read(self, folder: Path) -> dict[str, Any]:
        run: dict[str, Any] = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        if run.get("status") == "running" and run.get("id") not in self._active:
            # The server stopped while this run was going; its steps are gone.
            run["status"] = "interrupted"
        return run

    def _execute(
        self,
        folder: Path,
        run: dict[str, Any],
        worktrees: dict[str, Path],
        cancel: threading.Event,
        on_change: Callable[[], None],
    ) -> None:
        failed: set[str] = set()
        try:
            for step in run["steps"]:
                if cancel.is_set():
                    step["status"] = "cancelled"
                    continue
                if step["repository"] in failed:
                    step["status"] = "skipped"
                    continue
                step["status"] = "running"
                step["started_at"] = _now()
                _save(folder, run)
                on_change()
                status, code = self._run_step(folder / step["log"], step, worktrees, cancel)
                step.update(status=status, exit_code=code, finished_at=_now())
                if status != "passed":
                    failed.add(step["repository"])
                _save(folder, run)
                on_change()
            statuses = {step["status"] for step in run["steps"]}
            run["status"] = (
                "cancelled" if cancel.is_set() else "passed" if statuses <= {"passed"} else "failed"
            )
        except Exception as exc:  # A broken step must never leave the run "running".
            run["status"] = "failed"
            run["error"] = str(exc)
        finally:
            run["finished_at"] = _now()
            _save(folder, run)
            with self._lock:
                self._active.pop(run["id"], None)
            on_change()

    def _run_step(
        self,
        log_path: Path,
        step: dict[str, Any],
        worktrees: dict[str, Path],
        cancel: threading.Event,
    ) -> tuple[str, int | None]:
        workdir = worktrees[step["repository"]]
        deadline = time.monotonic() + step["timeout_minutes"] * 60
        with log_path.open("wb") as log:
            log.write(f"$ {' '.join(step['command'])}\n# in {workdir}\n\n".encode())
            log.flush()
            try:
                process = subprocess.Popen(
                    step["command"],
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                log.write(f"Could not start the step: {exc}\n".encode())
                return "failed", None
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() > deadline:
                    _stop(process)
                    reason = "Cancelled" if cancel.is_set() else "Timed out"
                    log.write(f"\n{reason}; the step was stopped.\n".encode())
                    return ("cancelled" if cancel.is_set() else "failed"), process.returncode
                time.sleep(self.poll_interval)
            log.write(f"\nExited with code {process.returncode}.\n".encode())
            return ("passed" if process.returncode == 0 else "failed"), process.returncode


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Terminate a step and everything it started, then kill it if it lingers."""
    for sent in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sent)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _save(folder: Path, run: dict[str, Any]) -> None:
    atomic_write_text(folder / "run.json", json.dumps(run, indent=2) + "\n")

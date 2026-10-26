"""Build the complete read model the interface renders and streams.

The snapshot joins Alfred's persisted state with live tmux sessions and cached worktree status,
then derives per-task facts (agents involved, approval state, lifecycle journey, and which actions
are currently possible) using the same rules Alfred enforces.
"""

import re
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import alfred
from alfred.application.tasks import APPROVAL_RESETTING_EVENTS, APPROVAL_RESETTING_PREFIXES
from alfred.bootstrap import AlfredServices
from alfred.config.models import AlfredConfig
from alfred.domain.constants import (
    ExecutionMode,
    LifecyclePhase,
    PlanningState,
    RunStatus,
    TaskStatus,
    WorktreeMode,
)
from alfred.domain.models import AgentRun, Notification, Task
from alfred.domain.state_machine import ACTIVE_RUN_STATUSES, can_transition, can_transition_phase

import alfred_ui
from alfred_ui import artifacts
from alfred_ui.agents import describe_agent
from alfred_ui.artifacts import COMPLETION_BUCKETS, knowledge_counts
from alfred_ui.configedit import describe_config
from alfred_ui.tmux_inspector import SessionInfo, TmuxInspector
from alfred_ui.transcripts import TranscriptStore
from alfred_ui.workspace import NoWorkspaceError, WorkspaceContext

TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.CONSOLIDATED, TaskStatus.CANCELLED})
TERMINAL_PHASES = frozenset({LifecyclePhase.ARCHIVED, LifecyclePhase.CONSOLIDATED})
EVENT_TAIL = 400
AGENT_CHANGE = re.compile(r"Agent changed from (\S+) to (\S+)\.")

StateErrors = (ValueError, KeyError, TypeError, OSError)


def build_snapshot(
    context: WorkspaceContext,
    inspector: TmuxInspector,
    *,
    sessions: Sequence[SessionInfo] | None = None,
    worktrees: Mapping[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return the full read model, or a partial one describing why it cannot be built."""
    snapshot: dict[str, Any] = {
        "server_time": time.time(),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "versions": {"alfred": alfred.__version__, "ui": alfred_ui.__version__},
        "workspace": {
            "config_path": str(context.config_path or ""),
            "discovery_error": context.discovery_error,
            "generation": context.generation,
        },
        "tmux": {"available": inspector.available()},
        "error": None,
    }
    try:
        services = context.services()
    except NoWorkspaceError as exc:
        snapshot["error"] = {"kind": "no_workspace", "message": str(exc)}
        return snapshot
    except KeyError as exc:
        snapshot["error"] = {"kind": "config", "message": str(exc.args[0] if exc.args else exc)}
        return snapshot
    except StateErrors as exc:
        snapshot["error"] = {"kind": "config", "message": str(exc)}
        return snapshot

    config = services.config
    snapshot["config"] = describe_config(config)
    try:
        state = _read_state(services)
    except StateErrors as exc:
        snapshot["error"] = {"kind": "state", "message": str(exc)}
        return snapshot

    prefix = config.runtime.session_prefix
    live_sessions = sessions if sessions is not None else inspector.sessions()
    owned = {item.name: item for item in live_sessions if item.name.startswith(f"{prefix}-")}
    snapshot["tmux"]["server_running"] = bool(live_sessions) or inspector.server_running()
    snapshot["tmux"]["foreign_sessions"] = len(live_sessions) - len(owned)
    snapshot.update(
        _assemble(services, state, owned, dict(worktrees or {}), knowledge_counts(config))
    )
    return snapshot


class _State:
    """Typed view of Alfred's five state documents."""

    def __init__(
        self,
        tasks: tuple[Task, ...],
        runs: tuple[AgentRun, ...],
        queue: list[int],
        notifications: list[Notification],
        events: list[dict[str, Any]],
    ) -> None:
        self.tasks = tasks
        self.runs = runs
        self.queue = queue
        self.notifications = notifications
        self.events = events


def _read_state(services: AlfredServices) -> _State:
    events = [{"index": index, **record} for index, record in enumerate(services.store.events())]
    return _State(
        tasks=services.tasks.list(),
        runs=services.runs.list(),
        queue=services.store.queue(),
        notifications=[Notification.from_dict(item) for item in services.store.notifications()],
        events=events,
    )


def _assemble(
    services: AlfredServices,
    state: _State,
    sessions: dict[str, SessionInfo],
    worktrees: dict[int, list[dict[str, Any]]],
    knowledge: dict[int, int],
) -> dict[str, Any]:
    config = services.config
    prefix = config.runtime.session_prefix
    agents = {alias: describe_agent(agent) for alias, agent in config.agents.items()}
    runs_by_task: dict[int, list[AgentRun]] = defaultdict(list)
    for run in state.runs:
        runs_by_task[run.task_number].append(run)
    events_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in state.events:
        number = event.get("task_number")
        if isinstance(number, int):
            events_by_task[number].append(event)
    notifications_by_task: dict[int, list[Notification]] = defaultdict(list)
    for notification in state.notifications:
        notifications_by_task[notification.task_number].append(notification)
    pending_completions = _pending_completion_tasks(config.runtime.temp_directory)
    transcripts = _transcript_counts(config)

    tasks = []
    for task in state.tasks:
        derived = derive_task(
            task,
            runs_by_task.get(task.task_number, []),
            events_by_task.get(task.task_number, []),
            notifications_by_task.get(task.task_number, []),
            sessions=sessions,
            agents=agents,
            worktrees=worktrees.get(task.task_number, []),
            knowledge=knowledge.get(task.task_number, 0),
            queued=task.task_number in state.queue,
            completion_pending=task.task_number in pending_completions,
            transcripts=transcripts.get(task.task_number, 0),
        )
        tasks.append({**_task_dict(task), "derived": derived})

    coordinator_name = f"{prefix}-coordinator"
    learner_name = f"{prefix}-learner"
    marker = services.learner.marker
    learner_alias = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    run_sessions = {run.session_name: run for run in state.runs if run.session_name}
    return {
        "tasks": tasks,
        "runs": [_run_dict(run) for run in state.runs],
        "queue": state.queue,
        "notifications": [item.to_dict() for item in state.notifications],
        "events": state.events[-EVENT_TAIL:],
        "event_count": len(state.events),
        "sessions": [
            describe_session(item, prefix, run_sessions.get(item.name))
            for item in sessions.values()
        ],
        "coordinator": {
            "session_name": coordinator_name,
            "running": coordinator_name in sessions,
        },
        "learner": {
            "session_name": learner_name,
            "running": learner_name in sessions,
            "agent": learner_alias,
        },
        "completions": _completion_counts(config.runtime.temp_directory),
        "knowledge": {"total": sum(knowledge.values()), "by_task": knowledge},
    }


def build_task_detail(
    services: AlfredServices,
    number: int,
    *,
    sessions: Sequence[SessionInfo],
    worktrees: Mapping[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return one task with its runs, full event history, files, and related tasks."""
    config = services.config
    task = services.tasks.require(number)
    state = _read_state(services)
    prefix = config.runtime.session_prefix
    owned = {item.name: item for item in sessions if item.name.startswith(f"{prefix}-")}
    runs = [run for run in state.runs if run.task_number == number]
    events = [event for event in state.events if event.get("task_number") == number]
    notifications = [item for item in state.notifications if item.task_number == number]
    agents = {alias: describe_agent(agent) for alias, agent in config.agents.items()}
    transcript_list = TranscriptStore.for_config(config).list(number)
    derived = derive_task(
        task,
        runs,
        events,
        notifications,
        sessions=owned,
        agents=agents,
        worktrees=list(worktrees.get(number, [])),
        knowledge=knowledge_counts(config).get(number, 0),
        queued=number in state.queue,
        completion_pending=number in _pending_completion_tasks(config.runtime.temp_directory),
        transcripts=len(transcript_list),
    )
    by_number = {item.task_number: item for item in state.tasks}
    drift = list(services.sync.validate(number)) if config.trackers.markdown.enabled else []
    return {
        "task": {**_task_dict(task), "derived": derived},
        "runs": [_run_dict(run) for run in runs],
        "events": events,
        "notifications": [item.to_dict() for item in notifications],
        "prompts": artifacts.prompt_files(config, number),
        "completions": artifacts.completion_files(config, number),
        "knowledge": artifacts.knowledge_entries(config, task_number=number),
        "transcripts": transcript_list,
        "drift": drift,
        "dependencies": [_related(by_number.get(item), item) for item in task.dependencies],
        "dependents": [
            _related(item, item.task_number) for item in state.tasks if number in item.dependencies
        ],
    }


def _related(task: Task | None, number: int) -> dict[str, Any]:
    if task is None:
        return {"task_number": number, "title": "", "status": "", "exists": False}
    return {
        "task_number": task.task_number,
        "title": task.title,
        "status": str(task.status),
        "phase": str(task.lifecycle_phase),
        "exists": True,
    }


def _transcript_counts(config: AlfredConfig) -> dict[int, int]:
    counts: dict[int, int] = {}
    for item in TranscriptStore.for_config(config).list():
        number = item.get("task_number")
        if isinstance(number, int):
            counts[number] = counts.get(number, 0) + 1
    return counts


def describe_session(
    session: SessionInfo, prefix: str, run: AgentRun | None = None
) -> dict[str, Any]:
    """Classify an Alfred-owned session as a task, coordinator, learner, or other session."""
    kind = "other"
    task_number: int | None = None
    agent_alias = ""
    if session.name == f"{prefix}-coordinator":
        kind = "coordinator"
    elif session.name == f"{prefix}-learner":
        kind = "learner"
    else:
        pattern = rf"{re.escape(prefix)}-(\d+)-([A-Za-z0-9][A-Za-z0-9_-]*)"
        match = re.fullmatch(pattern, session.name)
        if match:
            kind = "task"
            task_number = int(match.group(1))
            agent_alias = match.group(2)
    return {
        **session.to_dict(),
        "kind": kind,
        "task_number": task_number,
        "agent_alias": agent_alias,
        "run_id": run.run_id if run else "",
        "run_status": str(run.run_status) if run else "",
    }


def derive_task(
    task: Task,
    runs: list[AgentRun],
    events: list[dict[str, Any]],
    notifications: list[Notification],
    *,
    sessions: Mapping[str, SessionInfo],
    agents: Mapping[str, dict[str, Any]],
    worktrees: list[dict[str, Any]],
    knowledge: int,
    queued: bool,
    completion_pending: bool,
    transcripts: int = 0,
) -> dict[str, Any]:
    """Derive the facts the board and task views need for one task."""
    active = next((run for run in reversed(runs) if run.run_status in ACTIVE_RUN_STATUSES), None)
    latest = runs[-1] if runs else None
    shown = active or latest
    session_name = shown.session_name if shown is not None else ""
    session = sessions.get(session_name) if session_name else None
    pane = session.active_pane if session else None
    approved = approved_since_last_work(events)
    plan_reports = [event for event in events if event["event_type"] == "AGENT_PLAN_COMPLETED"]
    pending_notifications = [item for item in notifications if not item.acknowledged]
    return {
        "active_run": _run_dict(active) if active else None,
        "latest_run": _run_dict(latest) if latest else None,
        "run_count": len(runs),
        "session": {
            "name": session_name,
            "alive": session is not None,
            "attached": session.attached if session else 0,
            "activity": session.activity if session else 0,
            "command": pane.current_command if pane else "",
            "pid": pane.pid if pane else 0,
            "dead": pane.dead if pane else False,
            "width": pane.width if pane else 0,
            "height": pane.height if pane else 0,
        }
        if session_name
        else None,
        "agents": involved_agents(task, runs, events, agents, sessions),
        "approved_since_last_work": approved,
        "plan": {
            "required": task.execution_mode == ExecutionMode.PLAN_EXECUTION,
            "state": str(task.planning_state),
            "awaiting_approval": task.execution_mode == ExecutionMode.PLAN_EXECUTION
            and task.planning_state == PlanningState.STARTED,
            "reported": bool(plan_reports)
            and _after_last_plan_dispatch(events, plan_reports[-1]["index"]),
            "last_report": plan_reports[-1] if plan_reports else None,
        },
        "last_event": events[-1] if events else None,
        "event_count": len(events),
        "notifications": {
            "pending": len(pending_notifications),
            "types": sorted({item.notification_type for item in pending_notifications}),
        },
        "worktrees": worktrees,
        "dirty": any(item.get("dirty") for item in worktrees),
        "knowledge_count": knowledge,
        "queued": queued,
        "completion_pending": completion_pending,
        "transcripts": transcripts,
        "journey": journey(task, runs, events, approved),
        "actions": available_actions(task, active, runs, approved, worktrees),
    }


def approved_since_last_work(events: Iterable[dict[str, Any]]) -> bool:
    """Mirror Alfred's merge rule: an approval must follow the latest work or status change."""
    for event in reversed(list(events)):
        event_type = str(event.get("event_type", ""))
        if event_type == "REVIEW_APPROVED":
            return True
        if event_type in APPROVAL_RESETTING_EVENTS or event_type.startswith(
            APPROVAL_RESETTING_PREFIXES
        ):
            return False
    return False


def involved_agents(
    task: Task,
    runs: list[AgentRun],
    events: list[dict[str, Any]],
    agents: Mapping[str, dict[str, Any]],
    sessions: Mapping[str, SessionInfo],
) -> list[dict[str, Any]]:
    """Return every agent that was assigned to, ran, or reported on the task."""
    records: dict[str, dict[str, Any]] = {}

    def record(alias: str) -> dict[str, Any]:
        if alias not in records:
            described = agents.get(alias, {})
            records[alias] = {
                "alias": alias,
                "configured": alias in agents,
                "assigned": False,
                "cli": described.get("cli", ""),
                "model": described.get("model", ""),
                "runtime_target": described.get("runtime_target", ""),
                "runs": 0,
                "events": 0,
                "last_seen": "",
                "live": False,
                "roles": [],
            }
        return records[alias]

    if task.assigned_agent_alias:
        entry = record(task.assigned_agent_alias)
        entry["assigned"] = True
        entry["roles"].append("assigned")
    for run in runs:
        entry = record(run.agent_alias)
        entry["runs"] += 1
        entry["last_seen"] = max(entry["last_seen"], run.last_event_at or run.started_at)
        if run.session_name in sessions and run.run_status in ACTIVE_RUN_STATUSES:
            entry["live"] = True
        if "ran" not in entry["roles"]:
            entry["roles"].append("ran")
    for event in events:
        actor = str(event.get("actor", ""))
        if actor.startswith("agent:"):
            entry = record(actor.split(":", 1)[1])
            entry["events"] += 1
            entry["last_seen"] = max(entry["last_seen"], str(event.get("timestamp", "")))
            if "reported" not in entry["roles"]:
                entry["roles"].append("reported")
        match = AGENT_CHANGE.fullmatch(str(event.get("details", "")))
        if event.get("event_type") == "AGENT_ASSIGNED" and match:
            for alias in match.groups():
                if alias != "unassigned":
                    entry = record(alias)
                    if not {"assigned", "was assigned"} & set(entry["roles"]):
                        entry["roles"].append("was assigned")
    return sorted(
        records.values(), key=lambda item: (not item["assigned"], not item["live"], item["alias"])
    )


def journey(
    task: Task,
    runs: list[AgentRun],
    events: list[dict[str, Any]],
    approved: bool,
) -> list[dict[str, str]]:
    """Return the task's position along Alfred's delivery lifecycle."""
    types = {str(event.get("event_type", "")) for event in events}
    phase = task.lifecycle_phase
    consolidated = phase == LifecyclePhase.CONSOLIDATED
    delivered = phase in {LifecyclePhase.TESTING_DEPLOYMENT, LifecyclePhase.ARCHIVED}
    plan_mode = task.execution_mode == ExecutionMode.PLAN_EXECUTION
    steps: list[tuple[str, str, bool]] = [
        ("created", "Created", True),
        ("assigned", "Agent assigned", bool(task.assigned_agent_alias)),
        ("dispatched", "Planning started" if plan_mode else "Agent dispatched", bool(runs)),
    ]
    if plan_mode:
        # Planning is itself a dispatch, so approval follows it.
        steps.append(
            (
                "plan",
                "Plan approved",
                task.planning_state in {PlanningState.APPROVED, PlanningState.COMPLETED}
                or "PLAN_APPROVED" in types,
            )
        )
    steps.extend(
        [
            ("reported", "Completion reported", "RUN_COMPLETED" in types or delivered),
            ("approved", "Review approved", approved or delivered),
            ("merged", "Merged", delivered),
            (
                "deployed",
                "Deployed",
                task.status == TaskStatus.COMPLETED and not consolidated,
            ),
            ("archived", "Archived", phase == LifecyclePhase.ARCHIVED),
        ]
    )
    result: list[dict[str, str]] = []
    current_found = False
    for key, label, done in steps:
        if consolidated and not done:
            state = "skipped"
        elif done:
            state = "done"
        elif not current_found:
            current_found = True
            held = task.status in {TaskStatus.BLOCKED, TaskStatus.ON_HOLD}
            state = "blocked" if held else "current"
        else:
            state = "pending"
        result.append({"key": key, "label": label, "state": state})
    if consolidated:
        result.append({"key": "consolidated", "label": "Consolidated", "state": "done"})
    return result


def available_actions(
    task: Task,
    active: AgentRun | None,
    runs: list[AgentRun],
    approved: bool,
    worktrees: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return each action with whether Alfred would currently accept it, and why not."""
    status = task.status
    phase = task.lifecycle_phase
    terminal = status in TERMINAL_STATUSES or phase in TERMINAL_PHASES
    plan_mode = task.execution_mode == ExecutionMode.PLAN_EXECUTION
    running = active is not None and active.run_status != RunStatus.QUEUED
    actions: dict[str, dict[str, Any]] = {}

    def decide(name: str, reason: str) -> None:
        actions[name] = {"enabled": not reason, "reason": reason}

    decide(
        "trigger",
        "The task is finished"
        if terminal
        else "Assign an agent first"
        if not task.assigned_agent_alias
        else f"A {active.run_status} run is active; stop it before triggering again"
        if running and active is not None
        else "The plan is awaiting approval; approve it to continue"
        if plan_mode and task.planning_state == PlanningState.STARTED and active is None
        else "",
    )
    decide(
        "continue",
        "Only plan-execution tasks have a plan to approve"
        if not plan_mode
        else "No plan is awaiting approval"
        if task.planning_state != PlanningState.STARTED
        else "No active planning run"
        if active is None
        else "",
    )
    decide("stop", "" if active is not None else "No active run to stop")
    decide(
        "reopen",
        "The task is finished"
        if terminal
        else "A run is already active"
        if active is not None
        else "Assign an agent first"
        if not task.assigned_agent_alias
        else "No earlier run to reopen; trigger the task instead"
        if not runs
        else "",
    )
    decide("event", "The task is finished" if terminal else "")
    decide("complete", "" if active is not None else "No active run to complete")
    decide("progress", _transition_reason(status, TaskStatus.IN_PROGRESS))
    decide("start", _transition_reason(status, TaskStatus.IN_PROGRESS))
    decide("block", _transition_reason(status, TaskStatus.BLOCKED))
    decide(
        "unblock",
        ""
        if status in {TaskStatus.BLOCKED, TaskStatus.ON_HOLD}
        else "The task is not blocked or on hold",
    )
    decide(
        "hold",
        "The task is already on hold"
        if status == TaskStatus.ON_HOLD
        else _transition_reason(status, TaskStatus.ON_HOLD),
    )
    approve_reason = _transition_reason(status, TaskStatus.IN_REVIEW)
    changes_reason = _transition_reason(status, TaskStatus.IN_PROGRESS)
    # Offer review when approval is possible; requesting changes alone (Pending -> In Progress)
    # is allowed by the state machine but is not a review of delivered work.
    decide("review", approve_reason)
    actions["review"]["approve"] = not approve_reason
    actions["review"]["request_changes"] = not changes_reason
    decide(
        "merge",
        f"Finish or stop the {active.run_status} run first"
        if active is not None
        else f"Merge needs MR in Review; status is {status}"
        if status != TaskStatus.IN_REVIEW
        else "Record a review approval after the latest work first"
        if not approved
        else "Already merged"
        if phase == LifecyclePhase.TESTING_DEPLOYMENT
        else f"Lifecycle is {phase}; only active tasks can be merged"
        if not can_transition_phase(phase, LifecyclePhase.TESTING_DEPLOYMENT)
        else "",
    )
    decide(
        "deploy",
        f"Finish or stop the {active.run_status} run first"
        if active is not None
        else "Merge the task first (testing and deployment phase)"
        if phase != LifecyclePhase.TESTING_DEPLOYMENT
        else _transition_reason(status, TaskStatus.COMPLETED),
    )
    decide(
        "archive",
        f"Finish or stop the {active.run_status} run first"
        if active is not None
        else "Archive is available after merge (testing and deployment phase)"
        if phase != LifecyclePhase.TESTING_DEPLOYMENT
        else "",
    )
    decide(
        "consolidate",
        f"Finish or stop the {active.run_status} run first"
        if active is not None
        else f"Only active tasks can be consolidated; lifecycle is {phase}"
        if phase != LifecyclePhase.ACTIVE
        else "",
    )
    # Cancel stops a live run itself, so only the status and lifecycle limit it.
    decide(
        "cancel",
        "The task is finished" if terminal else _transition_reason(status, TaskStatus.CANCELLED),
    )
    decide("assign", "The task is finished" if terminal else "")
    decide(
        "reassign",
        "The task is finished"
        if terminal
        else "Assign an agent first"
        if not task.assigned_agent_alias
        else "",
    )
    decide("update", "")
    decide(
        "worktree_create",
        "Worktrees are disabled for this task"
        if task.worktree_mode == WorktreeMode.DISABLED
        else "Set a branch name first"
        if not task.branch_name.strip()
        else "",
    )
    decide(
        "commit",
        ""
        if any(item.get("dirty") for item in worktrees)
        else "No uncommitted changes"
        if worktrees
        else "No worktrees",
    )
    decide("push", "" if worktrees else "No worktrees")
    decide(
        "remove_worktrees",
        "No worktrees"
        if not worktrees
        else "Stop the active run first; stopping can also remove worktrees"
        if active is not None
        else "",
    )
    return actions


def _transition_reason(current: TaskStatus, target: TaskStatus) -> str:
    return "" if can_transition(current, target) else f"{current} cannot move to {target}"


def _after_last_plan_dispatch(events: list[dict[str, Any]], report_index: int) -> bool:
    dispatches = [
        event["index"]
        for event in events
        if event["event_type"] in {"RUN_STARTED", "RUN_QUEUED"}
        and str(event.get("details", "")).startswith("Plan phase")
    ]
    return not dispatches or report_index > dispatches[-1]


def _pending_completion_tasks(temp_directory: Path) -> set[int]:
    directory = temp_directory / "completions" / "pending"
    if not directory.is_dir():
        return set()
    numbers: set[int] = set()
    for path in directory.glob("task-*.json"):
        match = re.fullmatch(r"task-(\d+)(?:-\d+)?\.json", path.name)
        if match:
            numbers.add(int(match.group(1)))
    return numbers


def _completion_counts(temp_directory: Path) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for bucket in COMPLETION_BUCKETS:
        directory = temp_directory / "completions" / bucket
        counts[bucket] = len(list(directory.glob("task-*.json"))) if directory.is_dir() else 0
    # When the oldest report started waiting, so "waiting" calls keep a stable time.
    pending = temp_directory / "completions" / "pending"
    stamps = (
        [path.stat().st_mtime for path in pending.glob("task-*.json")] if pending.is_dir() else []
    )
    counts["oldest_pending"] = (
        datetime.fromtimestamp(min(stamps), UTC).isoformat(timespec="seconds") if stamps else None
    )
    return counts


def _task_dict(task: Task) -> dict[str, Any]:
    return {key: _plain(value) for key, value in task.to_dict().items()}


def _run_dict(run: AgentRun) -> dict[str, Any]:
    return {key: _plain(value) for key, value in run.to_dict().items()}


def _plain(value: Any) -> Any:
    if isinstance(value, str):
        return str(value)
    return value

"""Serializable task, run, event, and completion detail objects."""

from dataclasses import asdict, dataclass, field
from typing import Any

from alfred.domain.constants import (
    CompletionStatus,
    DispatchMode,
    ExecutionMode,
    LifecyclePhase,
    PlanningState,
    RunStatus,
    TaskStatus,
    TaskType,
    WorktreeMode,
)


@dataclass(slots=True)
class Task:
    """A unit of work managed through Alfred's lifecycle."""

    task_number: int
    title: str
    description: str
    category: str = "General"
    task_type: TaskType = TaskType.DEVELOPMENT
    model: str = ""
    priority: str = "P2"
    status: TaskStatus = TaskStatus.PENDING
    deadline: str = ""
    notes: str = ""
    dependencies: list[int] = field(default_factory=list)
    assigned_agent_alias: str = ""
    dispatch_mode: DispatchMode = DispatchMode.AUTO
    execution_mode: ExecutionMode = ExecutionMode.DIRECT
    worktree_mode: WorktreeMode = WorktreeMode.ENABLED
    branch_name: str = ""
    planning_state: PlanningState = PlanningState.NOT_REQUIRED
    lifecycle_phase: LifecyclePhase = LifecyclePhase.ACTIVE
    target_repositories: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        """Load current or legacy task JSON into the typed model."""
        normalized = dict(data)
        normalized["created_at"] = normalized.pop(
            "created_at_ist", normalized.get("created_at", "")
        )
        normalized["updated_at"] = normalized.pop(
            "updated_at_ist", normalized.get("updated_at", "")
        )
        normalized.setdefault("target_repositories", normalized.pop("repos", []))
        normalized["task_type"] = TaskType(normalized.get("task_type", TaskType.DEVELOPMENT))
        normalized["status"] = TaskStatus(normalized.get("status", TaskStatus.PENDING))
        normalized["dispatch_mode"] = DispatchMode(
            normalized.get("dispatch_mode", DispatchMode.AUTO)
        )
        normalized["execution_mode"] = ExecutionMode(
            normalized.get("execution_mode", ExecutionMode.DIRECT)
        )
        normalized["worktree_mode"] = WorktreeMode(
            normalized.get("worktree_mode", WorktreeMode.ENABLED)
        )
        normalized["planning_state"] = PlanningState(
            normalized.get("planning_state", PlanningState.NOT_REQUIRED)
        )
        normalized["lifecycle_phase"] = LifecyclePhase(
            normalized.get("lifecycle_phase", LifecyclePhase.ACTIVE)
        )
        return cls(**normalized)


@dataclass(slots=True)
class AgentRun:
    """One agent execution attempt for a task."""

    run_id: str
    task_number: int
    agent_alias: str
    runtime_target: str
    run_status: RunStatus
    started_at: str = ""
    ended_at: str = ""
    summary: str = ""
    phase: str = "execution"
    command_preview: str = ""
    worktree_paths: dict[str, str] = field(default_factory=dict)
    session_name: str = ""
    session_status: str = "inactive"
    last_event_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRun":
        """Load current or legacy run JSON into the typed model."""
        normalized = dict(data)
        for key in ("started_at", "ended_at", "last_event_at"):
            normalized[key] = normalized.pop(f"{key}_ist", normalized.get(key, ""))
        normalized["run_status"] = RunStatus(normalized["run_status"])
        normalized.setdefault("phase", "execution")
        normalized.setdefault("command_preview", "")
        normalized.setdefault("worktree_paths", {})
        normalized.setdefault("session_name", "")
        normalized.setdefault("session_status", "inactive")
        return cls(**normalized)


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """An auditable task lifecycle event."""

    timestamp: str
    actor: str
    event_type: str
    details: str

    def to_markdown(self) -> str:
        """Render one stable Markdown timeline row."""
        return f"- **{self.timestamp}** | `{self.actor}` | `{self.event_type}` | {self.details}"

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-compatible representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CompletionReport:
    """Structured handoff from an execution agent to the coordinator."""

    task_number: int
    agent: str
    status: CompletionStatus
    summary: str
    repositories: tuple[str, ...] = ()
    branches: dict[str, str] = field(default_factory=dict)
    commits: tuple[str, ...] = ()
    knowledge_entries: int = 0
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompletionReport":
        """Normalize both ``failure`` and ``failed`` legacy completion values."""
        normalized = dict(data)
        raw_status = normalized.get("status")
        if raw_status == "failure":
            raw_status = CompletionStatus.FAILED
        if not isinstance(raw_status, (str, CompletionStatus)):
            raise ValueError("Completion status is required")
        normalized["status"] = CompletionStatus(raw_status)
        normalized["repositories"] = tuple(
            normalized.pop("repos_touched", normalized.get("repositories", ()))
        )
        normalized["commits"] = tuple(normalized.get("commits", ()))
        normalized["knowledge_entries"] = normalized.pop(
            "knowledge_entries_written", normalized.get("knowledge_entries", 0)
        )
        return cls(**normalized)


@dataclass(slots=True)
class Notification:
    """Human-review notification generated by an Alfred workflow."""

    notification_id: str
    notification_type: str
    task_number: int
    created_at: str
    details: dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False
    acknowledged_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Notification":
        """Load a notification record."""
        return cls(**data)


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """One durable learning contributed by a completed task."""

    task_number: int
    category: str
    title: str
    content: str
    created_at: str
    agent: str = ""
    related_files: tuple[str, ...] = ()
    related_repositories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MergeRequestOptions:
    """Ask a GitLab remote to open a merge request for a pushed task branch."""

    title: str = ""
    description: str = ""

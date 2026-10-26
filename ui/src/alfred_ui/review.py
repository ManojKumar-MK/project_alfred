"""Pull-request style review of a task's branch: line comments, the verdict, and review links.

Alfred records the review decision itself (``alfred task review``); this store keeps what the
decision was based on. Reviews live under the private temp directory next to transcripts, one
versioned JSON document per task.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from alfred.config.models import AlfredConfig
from alfred.utils.files import atomic_write_text

REVIEW_VERSION = 1
MAX_COMMENT_CHARACTERS = 20_000
MAX_COMMENTS = 2_000
Side = Literal["old", "new"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _empty(task_number: int) -> dict[str, Any]:
    return {
        "version": REVIEW_VERSION,
        "task_number": task_number,
        "comments": [],
        "verdict": None,
        "links": [],
    }


class ReviewStore:
    """Read and change the review document of each task in one workspace."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def for_config(cls, config: AlfredConfig) -> "ReviewStore":
        """Return the store for a workspace configuration."""
        return cls(config.runtime.temp_directory / "ui" / "reviews")

    def path(self, task_number: int) -> Path:
        """Return the document path for one task."""
        return self.directory / f"task-{int(task_number)}.json"

    def load(self, task_number: int) -> dict[str, Any]:
        """Return a task's review, or an empty one; an unknown version is rejected."""
        path = self.path(task_number)
        if not path.is_file():
            return _empty(task_number)
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("version") != REVIEW_VERSION:
            raise ValueError(f"Unsupported review document {path}")
        return document

    def save(self, document: dict[str, Any]) -> dict[str, Any]:
        """Write a review document atomically and return it."""
        path = self.path(int(document["task_number"]))
        atomic_write_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")
        return document

    def add_comment(
        self,
        task_number: int,
        *,
        repository: str,
        path: str,
        line: int | None,
        side: Side,
        body: str,
        author: str,
    ) -> dict[str, Any]:
        """Add a comment on a line, or on the whole file when ``line`` is ``None``."""
        text = _comment_text(body)
        if not repository.strip() or not path.strip():
            raise ValueError("A review comment needs a repository and a file path")
        if line is not None and line < 1:
            raise ValueError("Line numbers start at 1")
        document = self.load(task_number)
        if len(document["comments"]) >= MAX_COMMENTS:
            raise ValueError(f"A review holds at most {MAX_COMMENTS} comments")
        comment = {
            "id": uuid.uuid4().hex,
            "repository": repository.strip(),
            "path": path.strip(),
            "line": line,
            "side": side,
            "body": text,
            "author": author.strip() or "manager",
            "created_at": _now(),
            "updated_at": None,
            "resolved": False,
            "sent_at": None,
        }
        document["comments"].append(comment)
        self.save(document)
        return comment

    def update_comment(
        self,
        task_number: int,
        comment_id: str,
        *,
        body: str | None = None,
        resolved: bool | None = None,
    ) -> dict[str, Any]:
        """Edit a comment's text or resolve and reopen it."""
        document = self.load(task_number)
        comment = _find(document, comment_id)
        if body is not None:
            comment["body"] = _comment_text(body)
            comment["updated_at"] = _now()
        if resolved is not None:
            comment["resolved"] = resolved
        self.save(document)
        return comment

    def delete_comment(self, task_number: int, comment_id: str) -> None:
        """Remove a comment."""
        document = self.load(task_number)
        _find(document, comment_id)
        document["comments"] = [item for item in document["comments"] if item["id"] != comment_id]
        self.save(document)

    def outstanding(self, task_number: int) -> list[dict[str, Any]]:
        """Return unresolved comments the agent has not been sent yet."""
        return [
            item
            for item in self.load(task_number)["comments"]
            if not item["resolved"] and not item["sent_at"]
        ]

    def record_verdict(
        self,
        task_number: int,
        decision: Literal["approved", "changes_requested"],
        actor: str,
        *,
        sent: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Remember the decision and mark the comments delivered with it as sent."""
        document = self.load(task_number)
        stamp = _now()
        document["verdict"] = {"decision": decision, "actor": actor, "at": stamp}
        for comment in document["comments"]:
            if comment["id"] in sent:
                comment["sent_at"] = stamp
        return self.save(document)

    def add_links(self, task_number: int, links: list[dict[str, str]]) -> dict[str, Any]:
        """Remember review links (merge or pull requests) printed by a push, newest first."""
        document = self.load(task_number)
        known = {(item["repository"], item["url"]) for item in document["links"]}
        for link in links:
            if (link["repository"], link["url"]) not in known:
                document["links"].insert(0, {**link, "at": _now()})
        return self.save(document)


def feedback_message(task_number: int, comments: list[dict[str, Any]], summary: str) -> str:
    """Render review comments as one message an agent can act on."""
    lines = [f"Review of task {task_number}: changes requested."]
    if summary.strip():
        lines += ["", summary.strip()]
    if comments:
        lines += ["", "Address each comment, commit, then report progress:"]
    for index, comment in enumerate(comments, start=1):
        where = f"{comment['repository']}/{comment['path']}"
        if comment["line"] is not None:
            side = " (removed line)" if comment["side"] == "old" else ""
            where += f":{comment['line']}{side}"
        body = "\n   ".join(comment["body"].splitlines())
        lines.append(f"{index}. {where}\n   {body}")
    return "\n".join(lines)


def _comment_text(body: str) -> str:
    text = body.strip()
    if not text:
        raise ValueError("A review comment cannot be empty")
    if len(text) > MAX_COMMENT_CHARACTERS:
        raise ValueError(f"A review comment is limited to {MAX_COMMENT_CHARACTERS} characters")
    return text


def _find(document: dict[str, Any], comment_id: str) -> dict[str, Any]:
    for comment in document["comments"]:
        if comment["id"] == comment_id:
            return comment  # type: ignore[no-any-return]
    raise KeyError(f"Review comment {comment_id} not found")

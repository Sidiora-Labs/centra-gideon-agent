"""Request policies and text views for native application tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit


class InputIssue(ValueError):
    def __init__(self, message: str, *hints: str):
        super().__init__(message)
        self.hints = list(hints)


def integer_option(arguments: dict, name: str, fallback: int) -> int:
    try:
        return int(arguments.get(name, fallback) or fallback)
    except (ValueError, TypeError):
        return fallback


def web_url(value: str) -> bool:
    try:
        return urlsplit(value).scheme.lower() in {"http", "https"}
    except ValueError:
        return False


@dataclass(frozen=True)
class KnowledgeDraft:
    kind: str
    title: str
    content: str
    url: str
    tags: list[str]
    extra: dict[str, Any]

    @classmethod
    def parse(cls, arguments: dict) -> KnowledgeDraft:
        kind = str(arguments.get("type", "note")).strip() or "note"
        if kind not in {"note", "fleeting", "journal", "gist", "bookmark"}:
            raise InputIssue(
                f"knowledge_create: unsupported type {kind!r} (agents author text/bookmark types)",
                "Use one of: note, fleeting, journal, gist, bookmark.",
            )
        title = str(arguments.get("title", "")).strip()
        content = str(arguments.get("content", ""))
        url = str(arguments.get("url", "")).strip()
        if kind == "bookmark":
            if not url:
                raise InputIssue("knowledge_create: bookmark requires a url")
            if not web_url(url):
                raise InputIssue("knowledge_create: bookmark url must be http(s)")
        if not (title or content.strip() or url):
            raise InputIssue("knowledge_create: title, content, or url required")
        if kind == "journal" and not title:
            title = datetime.now().strftime("%B %-d, %Y")
        extra = {"processing_status": "queued"}
        if kind == "gist" and (
            language := str(arguments.get("gist_language", "")).strip()
        ):
            extra["gist_language"] = language
        tags = arguments.get("tags")
        return cls(
            kind,
            title,
            content,
            url,
            [str(tag) for tag in tags] if isinstance(tags, list) else [],
            extra,
        )

    def save(self, store) -> tuple[str, bool]:
        if self.kind == "bookmark" and self.url:
            existing = store.find_active_by_url(self.url)
            if existing:
                return existing["id"], False
        identifier = store.create_typed_item(
            item_type=self.kind,
            title=self.title or self.url or self.content[:60] or "Untitled",
            content=self.content,
            tags=self.tags,
            url=self.url,
            provider="native",
            extra=self.extra,
        )
        assert identifier is not None
        return identifier, True


@dataclass
class KnowledgePatch:
    fields: dict[str, Any]
    language: str | None

    @classmethod
    def parse(cls, arguments: dict) -> KnowledgePatch:
        fields = {
            key: str(arguments[key])
            for key in ("title", "content", "url")
            if key in arguments
        }
        url = str(fields.get("url", "")).strip()
        if url and not web_url(url):
            raise InputIssue("knowledge_update: url must be http(s)")
        if isinstance(arguments.get("tags"), list):
            fields["tags"] = list(map(str, arguments["tags"]))
        for flag in ("is_pinned", "is_archived"):
            if flag in arguments:
                fields[flag] = int(bool(arguments[flag]))
        language = (
            str(arguments["gist_language"]).strip()
            if "gist_language" in arguments
            else None
        )
        if not fields and language is None:
            raise InputIssue(
                "knowledge_update: no updatable fields given",
                "Pass at least one of: title, content, tags, url, gist_language, is_pinned, is_archived.",
            )
        return cls(fields, language)

    @property
    def reingest(self) -> bool:
        return bool({"content", "url"}.intersection(self.fields))

    def apply(self, store, identifier: str) -> str:
        item = store.get_item(identifier)
        if not item:
            return "not_found"
        journal = (item.get("item_type") or item.get("type")) == "journal"
        if journal and {"content", "title"}.intersection(self.fields):
            created = str(item.get("created_at") or "")[:10]
            if created and created != datetime.now().isoformat()[:10]:
                return "journal_locked"
        if (
            self.language is not None
            and (item.get("type") or item.get("item_type")) == "gist"
        ):
            self.fields["gist_language"] = self.language
        if not self.fields:
            return "noop"
        store.update_item(identifier, **self.fields)
        store.db.commit()
        return "ok"


@dataclass(frozen=True)
class KnowledgeDocument:
    item: dict

    def notices(self) -> list[str]:
        result = []
        state = self.item.get("processing_status") or ""
        if state in {"queued", "processing"}:
            result.append(
                "(still enriching — summary, tags, and insights may not be ready yet)"
            )
        if self.item.get("is_archived"):
            result.append(
                "(archived — the user has put this item away; treat as retired unless they ask about it)"
            )
        if state == "unreachable":
            result.append(
                "(unreachable — the page couldn't be fetched, so only the URL is saved; the content may be retrievable on a later retry)"
            )
        elif state == "failed":
            reason = (self.item.get("processing_error") or "").strip()
            result.append(
                f"(processing failed{': ' + reason if reason else ''} — content may be incomplete)"
            )
        return result

    def file_shape(self) -> str:
        if not self.item.get("file_path"):
            return ""
        metadata = self.item.get("file_metadata") or {}
        details = []
        if metadata.get("width") and metadata.get("height"):
            details.append(f"{metadata['width']}x{metadata['height']}")
        for key, label in (
            ("page_count", "pages"),
            ("sheet_count", "sheets"),
            ("slide_count", "slides"),
            ("row_count", "rows"),
        ):
            if metadata.get(key):
                details.append(f"{metadata[key]} {label}")
        if self.item.get("file_size"):
            size = self.item["file_size"] / 1024
            details.append(f"{size:.0f} KB" if size < 1024 else f"{size / 1024:.1f} MB")
        if self.item.get("mime_type"):
            details.append(self.item["mime_type"])
        return "file: " + ", ".join(details) if details else ""

    def render(self, title, redact) -> str:
        item = self.item
        kind = item.get("type", "note")
        language = (item.get("gist_language") or "").strip() if kind == "gist" else ""
        tag = f"{kind} · {language}" if kind == "gist" and language else kind
        rows = [f"# {title(item)} [{tag}]", *self.notices()]
        tags = ", ".join(item.get("tags", []) or [])
        if tags:
            rows.append(f"tags: {tags}")
        if item.get("url"):
            rows.append(f"url: {item.get('url')}")
        if shape := self.file_shape():
            rows.append(shape)
        insights = item.get("insights") or {}
        summary = (
            item.get("summary")
            or item.get("ai_summary")
            or insights.get("summary")
            or item.get("url_description")
            or ""
        ).strip()
        if summary:
            rows.append(f"\nsummary: {summary}")
        for field, heading in (
            ("key_points", "key points"),
            ("action_items", "action items"),
        ):
            entries = insights.get(field)
            if isinstance(entries, list) and entries:
                rows.append(
                    f"{heading}:\n" + "\n".join(f"- {entry}" for entry in entries)
                )
        if item.get("content"):
            rows.append(f"\n{item.get('content')}")
        return redact("\n".join(rows))


_TASK_FIELDS = (
    "description",
    "priority",
    "task_list_id",
    "labels",
    "due",
    "exit_criteria",
    "action_plan",
    "depends_on",
)
_STATUS_ALIASES = {
    "complete": "done",
    "completed": "done",
    "finished": "done",
    "todo": "open",
    "to_do": "open",
    "pending": "open",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "doing": "in_progress",
    "wip": "in_progress",
    "canceled": "cancelled",
    "won't_do": "cancelled",
}


def task_fields(arguments: dict, *, updating: bool) -> dict:
    names = (
        ("title", "description", "status", *_TASK_FIELDS[1:])
        if updating
        else _TASK_FIELDS
    )
    result = {name: arguments[name] for name in names if name in arguments}
    if "status" in result:
        incoming = str(result["status"]).strip().lower().replace(" ", "_")
        normalized = _STATUS_ALIASES.get(incoming, incoming)
        if normalized not in {"open", "in_progress", "done", "cancelled", "blocked"}:
            raise InputIssue(
                f"task_update: {result['status']!r} is not a valid status",
                "Use one of: open, in_progress, done, cancelled, blocked.",
            )
        result["status"] = normalized
    return result


@dataclass(frozen=True)
class TaskDocument:
    task: Any

    def line(self) -> str:
        task = self.task
        labels = [f"[{task.status.value}]", task.title, f"(id={task.id})"]
        if task.priority.value != "medium":
            labels.append(f"!{task.priority.value}")
        if task.project:
            labels.append(f"@{task.project}")
        criteria = task.exit_criteria or []
        if criteria:
            complete = sum(
                bool(entry.get("status") == "complete" or entry.get("met"))
                for entry in criteria
            )
            labels.append(f"{complete}/{len(criteria)} criteria")
        return " ".join(labels)

    def detail(self, first_line: str) -> str:
        data = self.task.to_dict()
        lines = [first_line]
        if self.task.description:
            lines.append(f"\n{self.task.description}")
        criteria = data.get("exit_criteria") or []
        if criteria:
            lines += [
                "\nExit criteria:",
                *(
                    f"  [{'x' if item.get('met') else ' '}] {item.get('description', '')}"
                    for item in criteria
                ),
            ]
        plan = data.get("action_plan") or []
        if plan:
            lines += [
                "\nAction plan:",
                *(
                    f"  {item.get('sequence', index)}. {item.get('content', '')}"
                    for index, item in enumerate(plan)
                ),
            ]
        prerequisites = self.task.prerequisite_ids()
        if prerequisites:
            lines.append("\nDepends on: " + ", ".join(prerequisites))
        return "\n".join(lines)


class DecisionDocument:
    @staticmethod
    def created(row: dict) -> str:
        return "\n".join(
            (
                f"Logged decision '{row['summary']}' (id={row['id']}, {row['domain']}).",
                f"Expecting: {row['expectation']} at {row['confidence']:.0%} confidence.",
                f"One review scheduled for {row['review_horizon']} (trigger {row['reminder_trigger_id']}).",
            )
        )

    @staticmethod
    def listing(rows: list[dict]) -> str:
        lines = []
        for row in rows:
            parts = [
                f"[{row['status']}]",
                str(row["summary"]),
                f"(id={row['id']})",
                f"@{row['domain']}",
            ]
            if row["status"] == "pending":
                parts.append(f"review {row['review_horizon']}")
                if row.get("overdue"):
                    parts.append("OVERDUE")
                if row.get("stale_pending"):
                    parts.append("stale (deferred twice)")
            elif row["outcome_grade"]:
                parts.append(f"→ {row['outcome_grade']}")
            lines.append("- " + " ".join(parts))
        return "\n".join(lines)

    @staticmethod
    def resolved(row: dict) -> str:
        if row["status"] == "pending":
            if row.get("stale_pending"):
                return f"Decision {row['id']} has been deferred twice already — leaving it pending with no further reminders. It shows as stale in the journal."
            return f"Too early — review rescheduled for {row['review_horizon']} (deferral {row['deferrals']} of 2)."
        lesson = row["lesson_memory_key"]
        tail = (
            f"Lesson written to memory as {lesson}."
            if lesson
            else "No lesson was written."
        )
        return f"Resolved '{row['summary']}' as {row['outcome_grade']} (expected: {row['expectation']}).\n{tail}"


async def project_operation(operation: str, arguments: dict):
    from gideon.engine.agents.native import sdlc_tools

    handler = getattr(sdlc_tools, "project_" + operation)
    return await handler(arguments)


@dataclass(frozen=True)
class EnrichmentRequest:
    identifier: str
    store_factory: Any
    embedding_factory: Any
    pool_factory: Any
    ingest: Any
    logger: Any

    async def run(self) -> None:
        try:
            store = self.store_factory()
            services = {
                "embedder": self.embedding_factory(),
                "insights_pool": self.pool_factory(),
            }
            await self.ingest(store, self.identifier, **services)
        except Exception:
            self.logger.debug(
                "background knowledge enrich failed for %s",
                self.identifier,
                exc_info=True,
            )

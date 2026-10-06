"""Inbox — persisted message state read by the dashboard Inbox page.

Holds the inbox entity (items, per-user/channel state, retention) that the
dashboard inbox handlers read and mutate. Live message ingestion is provided
separately by the message-source providers in ``gideon.integrations.inbox_providers``.
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.operations.durability import record_files
from gideon.security.security import redact_credentials, redact_exfiltration_urls
from gideon.workspace import notification_kinds


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

__all__ = [
    "Classification",
    "Confidence",
    "ItemStatus",
    "STATUS_OPEN",
    "is_open_status",
    "InboxStore",
    "InboxItem",
    "InboxState",
    "owner_username",
    "UserResolver",
    "ItemKind",
    "NON_CHANNEL_KINDS",
    "SOURCE_DECLARABLE_KINDS",
    "make_item_id",
    "polled_item_id",
    "thread_mute_key",
    "emit_attention_item",
    "emit_shared_knowledge_item",
    "evaluate_alert",
    "notify_inbox_alert",
    "redact_item",
]

_STATE_FILE = "inbox_state.json"
_ITEMS_FILE = "inbox.json"
_USER_CACHE_TTL = 86400
_verification_tasks: set[asyncio.Task] = set()
_verification_tasks_lock = threading.Lock()


def _read_record_document(path: Path, key: str) -> dict[str, Any]:
    if not path.exists():
        return {key: []}
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or not isinstance(document.get(key), list)
        or any(not isinstance(row, dict) for row in document[key])
    ):
        raise ValueError(f"{path.name} has an invalid {key} record envelope")
    return document


def _merge_record_changes(
    live: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Apply local record and field changes to the latest locked on-disk snapshot."""
    merged = {identity: dict(row) for identity, row in live.items()}
    for identity in baseline.keys() - current.keys():
        merged.pop(identity, None)
    for identity, row in current.items():
        previous = baseline.get(identity)
        if previous is None:
            target = merged.setdefault(identity, dict(row))
            for key, value in row.items():
                target.setdefault(key, value)
            continue
        target = merged.setdefault(identity, dict(row))
        for key in previous.keys() | row.keys():
            before, after = previous.get(key), row.get(key)
            if before == after:
                continue
            if key == "owner_states" and isinstance(after, dict):
                owner_states = dict(target.get(key, {}))
                before_states = previous.get(key, {})
                for owner in set(before_states) | set(after):
                    old, new = before_states.get(owner), after.get(owner)
                    if old == new:
                        continue
                    if owner in after:
                        owner_states[owner] = new
                    else:
                        owner_states.pop(owner, None)
                target[key] = owner_states
            elif key in row:
                target[key] = after
            else:
                target.pop(key, None)
    return merged


def owner_username() -> str:
    """The configured attribution handle, or ``""`` on a single-owner install."""
    try:
        from gideon.cognition.identity import current_username

        return current_username()
    except Exception:
        logger.debug("inbox owner identity unavailable", exc_info=True)
        return ""


class ItemStatus(str, Enum):
    """The attention lifecycle: PENDING → SEEN → HANDLED | DISMISSED.

    SENT predates the others and is specific to reply-drafts (a draft was sent at the
    source); it stays because those items exist on disk and it means something the other
    four don't.

    FILTERED (INU-6) is a fifth terminal-until-restored state: a verifiable kind whose rule
    opted into verification was REFUTED by the second-opinion pass, so its row was persisted
    but its notification withheld. Restore flips it back to PENDING and fires the withheld
    notification once — so a false positive is recoverable, never a silent drop.
    """

    PENDING = "pending"
    SEEN = "seen"
    SENT = "sent"
    DISMISSED = "dismissed"
    HANDLED = "handled"
    FILTERED = "filtered"


STATUS_OPEN = frozenset({ItemStatus.PENDING.value, ItemStatus.SEEN.value})
STATUS_CLOSED = frozenset(
    {
        ItemStatus.SENT.value,
        ItemStatus.DISMISSED.value,
        ItemStatus.HANDLED.value,
        ItemStatus.FILTERED.value,
    }
)
assert STATUS_OPEN.isdisjoint(STATUS_CLOSED) and STATUS_OPEN | STATUS_CLOSED == {
    status.value for status in ItemStatus
}


def is_open_status(status: str) -> bool:
    return status in STATUS_OPEN


class ItemKind(str, Enum):
    """What kind of thing is asking for attention.

    ``MESSAGE`` is the default so every item written before this existed stays valid —
    the inbox began as a channel-message surface, and that is exactly what those items are.
    """

    MESSAGE = "message"
    MENTION = "mention"
    EMAIL = "email"
    AGENT_REQUEST = "agent_request"
    PROPOSAL = "proposal"
    NEEDS_INPUT = "needs_input"
    DIGEST = "digest"
    SYSTEM = "system"
    USER_NOTE = "user_note"


NON_CHANNEL_KINDS = frozenset(
    {
        ItemKind.AGENT_REQUEST.value,
        ItemKind.PROPOSAL.value,
        ItemKind.NEEDS_INPUT.value,
        ItemKind.DIGEST.value,
        ItemKind.SYSTEM.value,
        ItemKind.USER_NOTE.value,
    }
)

SOURCE_DECLARABLE_KINDS = frozenset(
    {
        ItemKind.MESSAGE.value,
        ItemKind.MENTION.value,
        ItemKind.EMAIL.value,
    }
)


def make_item_id(kind: str, *, now: float | None = None) -> str:
    """An id for a non-channel item: ``{kind}_{uuid8}_{ts}``.

    **The trailing ``_{ts}`` is load-bearing.** ``InboxItem.ts`` rsplits the id on the last
    underscore, and sorting/retention both read that property — an id without a numeric
    tail would silently sort as if it had no timestamp. The uuid8 in the middle is what
    keeps two same-second items of the same kind distinct, which ``{channel}_{ts}`` got for
    free from the channel's own message ids.
    """
    stamp = time.time() if now is None else now
    return f"{kind}_{uuid.uuid4().hex[:8]}_{stamp:.6f}"


def polled_item_id(source: str, channel: str, message_id: str, *, now: float) -> str:
    """A stable path-safe id bound to a source, channel and provider message id."""
    import hashlib

    identity = "\0".join((str(source), str(channel), str(message_id)))
    digest = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:24]
    return f"message_{digest}_{float(now)}"


def thread_mute_key(source: str, channel: str, message_id: str) -> str:
    """One namespaced durable mute key shared by row actions and pollers."""
    import hashlib

    identity = "\0".join((str(source), str(channel), str(message_id)))
    return "inbox-thread:" + hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()


class Classification(str, Enum):
    NEEDS_REPLY = "needs_reply"
    FYI = "fyi"
    NOISE = "noise"


class Confidence(str, Enum):
    HIGH = "high"
    NEEDS_REVIEW = "needs_review"
    ESCALATE = "escalate"
    USER = "user"


_UPDATABLE_FIELD_TYPES: dict[str, type] = {
    "status": str,
    "draft": str,
    "classification": str,
    "confidence": str,
    "classified_by": str,
    "classify_error": str,
    "drafted_by": str,
    "favorited": bool,
}

_SHARED_FIELD_TYPES: dict[str, type] = {
    "owner": str,
    "owner_states": dict,
}

_WIRE_TYPE_NAMES: dict[type, str] = {
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
    dict: "object",
    list: "array",
    type(None): "null",
}


def _wire_type_name(t: type) -> str:
    return _WIRE_TYPE_NAMES.get(t, t.__name__)


class InboxFieldTypeError(ValueError):
    """An item-field write named a value of the wrong type.

    Raised BEFORE anything is persisted. The value is never echoed back — only the type
    names — because the caller controls it and it may be arbitrarily large.
    """

    def __init__(self, field: str, expected: type, value: Any) -> None:
        self.field = field
        super().__init__(
            f"{field} must be a {_wire_type_name(expected)}, "
            f"got {_wire_type_name(type(value))}"
        )


def validate_updatable_fields(fields: dict[str, Any]) -> None:
    """Raise :class:`InboxFieldTypeError` on the first wrong-typed field.

    One implementation, two call sites: the HTTP handler runs it *before* its dismiss /
    mute / favorite side effects (so a refused request mutates nothing), and
    :meth:`InboxStore.update` runs it again for the three callers that never touch HTTP.
    """
    for key, value in fields.items():
        expected = _UPDATABLE_FIELD_TYPES.get(key)
        if expected is not None and not isinstance(value, expected):
            raise InboxFieldTypeError(key, expected, value)


@dataclass
class InboxItem:
    """A message surfaced by Inbox with an optional draft reply."""

    id: str
    channel: str
    channel_name: str
    thread_ts: str | None
    message: str
    sender_id: str
    sender_name: str
    thread_context: list[dict[str, str]] = field(default_factory=list)
    classification: str = ""
    draft: str = ""
    confidence: str = ""
    status: str = ItemStatus.PENDING
    created_at: float = 0.0
    context_summary: str = ""
    source: str = "native"
    can_reply: bool = False
    reply_target: str = ""
    favorited: bool = False
    item_kind: str = ItemKind.MESSAGE.value
    refs: dict = field(default_factory=dict)
    owner: str = ""
    owner_states: dict[str, str] = field(default_factory=dict)
    replied_at: float = 0.0
    classified_by: str = ""
    classify_error: str = ""
    drafted_by: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts
        return d

    def belongs_to(self, username: str) -> bool:
        """Whether this row belongs to *username*.

        An unattributed legacy row belongs to the local owner, and without a configured
        username every row remains local. Those two rules keep the additive owner field a
        no-op for existing single-owner installations.
        """
        viewer = str(username or "").strip().lower()
        attributed = str(self.owner or "").strip().lower()
        return not viewer or not attributed or attributed == viewer

    def status_for(self, username: str) -> str:
        """This reader's state for a shared row, falling back to its legacy state."""
        viewer = str(username or "").strip().lower()
        if not viewer:
            return self.status
        value = self.owner_states.get(viewer)
        return value if isinstance(value, str) else self.status

    def set_status_for(self, username: str, status: str) -> None:
        """Set one reader's state without changing another reader's shared-inbox view."""
        viewer = str(username or "").strip().lower()
        if viewer:
            self.owner_states[viewer] = status
        else:
            self.status = status

    def to_owner_dict(self, username: str) -> dict:
        """Wire projection with ``status`` resolved for the requesting owner."""
        data = self.to_dict()
        data["status"] = self.status_for(username)
        return data

    @property
    def ts(self) -> str:
        """Message timestamp extracted from the item ID ({channel}_{ts})."""
        return self.id.rsplit("_", 1)[-1]

    @property
    def thread_key(self) -> str:
        """The source-owned root message key used by both mute writers and pollers."""
        return thread_mute_key(
            self.source or "native", self.channel, self.thread_ts or self.reply_target or self.id
        )

    @classmethod
    def from_dict(cls, d: dict) -> "InboxItem":
        """Rebuild an item from stored JSON, dropping what it cannot hold.

        Already tolerant of *unknown* keys (that is what kept `favorited` back-compatible).
        This extends the same tolerance to a wrong-typed value, and it is a repair, not
        belt-and-braces: a stored `draft` that is an object made `redact_item` raise
        ``TypeError`` for every reader, so one poisoned item took out `GET /api/inbox`
        entirely — permanently, because the poison was on disk. Dropping the value falls the
        field back to its dataclass default and the inbox loads again.

        Only fields in :data:`_UPDATABLE_FIELD_TYPES` are dropped, and every one of those has
        a default. A required field is left alone so a genuinely unreadable record still
        fails loudly at construction instead of being silently invented.
        """
        clean: dict[str, Any] = {}
        for key, value in d.items():
            if key not in cls.__dataclass_fields__:
                continue
            expected = _UPDATABLE_FIELD_TYPES.get(key) or _SHARED_FIELD_TYPES.get(key)
            if expected is not None and not isinstance(value, expected):
                logger.warning(
                    "inbox item %s: stored %s is a %s, not a %s — falling back to the default",
                    d.get("id", "<no id>"),
                    key,
                    _wire_type_name(type(value)),
                    _wire_type_name(expected),
                )
                continue
            clean[key] = value
        if "classified_by" not in d and clean.get("confidence") != Confidence.USER.value:
            if clean.get("source") not in {"native", "digest"}:
                clean["classification"] = ""
            clean["confidence"] = ""
        return cls(**clean)


class UserResolver:
    """Caches user id → display name (persisted with the inbox state). Names are
    resolved by the message source that has the channel client; this just stores them.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}

    def load(self, data: dict[str, Any]) -> None:
        for uid, entry in data.items():
            if isinstance(entry, dict):
                self._cache[uid] = (entry.get("name", uid), entry.get("ts", 0.0))

    def dump(self) -> dict[str, Any]:
        return {uid: {"name": n, "ts": ts} for uid, (n, ts) in self._cache.items()}

    def get_cached(self, user_id: str) -> str | None:
        entry = self._cache.get(user_id)
        if entry and (time.time() - entry[1]) < _USER_CACHE_TTL:
            return entry[0]
        return None

    def put(self, user_id: str, name: str) -> None:
        self._cache[user_id] = (name, time.time())


class InboxState:
    """Persists polling state, user cache, and dismissed/muted sets."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_dir() / _STATE_FILE)
        self.last_read_ts: dict[str, str] = {}
        self.channel_names: dict[str, str] = {}
        self.dismissed: set[str] = set()
        self.muted_threads: set[str] = set()
        self.active_threads: dict[str, dict[str, str]] = {}
        self.user_resolver = UserResolver()
        self._user_alias: str | None = None
        self._baseline: dict[str, Any] = self._snapshot()

    def _snapshot(self) -> dict[str, Any]:
        return {
            "last_read_ts": dict(self.last_read_ts),
            "channel_names": dict(self.channel_names),
            "dismissed": set(self.dismissed),
            "muted_threads": set(self.muted_threads),
            "user_cache": self.user_resolver.dump(),
        }

    def load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self.last_read_ts.clear()
                self.channel_names.clear()
                self.dismissed.clear()
                self.muted_threads.clear()
                self.last_read_ts.update(data.get("last_read_ts", {}))
                self.channel_names.update(data.get("channel_names", {}))
                self.dismissed.update(data.get("dismissed", []))
                self.muted_threads.update(data.get("muted_threads", []))
                self.user_resolver.load(data.get("user_cache", {}))
                self._baseline = self._snapshot()
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load inbox state, starting fresh")

    def save(self) -> None:
        try:
            with record_files.locked_store(self._path.parent) as root:
                try:
                    live = json.loads(self._path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    live = {}
                if not isinstance(live, dict):
                    raise ValueError("inbox_state.json must contain a JSON object")
                current = self._snapshot()
                merged = dict(live)
                for key in ("last_read_ts", "channel_names", "user_cache"):
                    baseline = self._baseline.get(key, {})
                    desired = current[key]
                    existing = merged.get(key, {})
                    if not isinstance(existing, dict):
                        raise ValueError(f"inbox_state.json field {key} must be an object")
                    combined = dict(existing)
                    for identity in set(baseline) | set(desired):
                        if baseline.get(identity) == desired.get(identity):
                            continue
                        if identity in desired:
                            combined[identity] = desired[identity]
                        else:
                            combined.pop(identity, None)
                    merged[key] = combined
                for key in ("dismissed", "muted_threads"):
                    baseline = set(self._baseline.get(key, set()))
                    desired = set(current[key])
                    existing = merged.get(key, [])
                    if not isinstance(existing, list):
                        raise ValueError(f"inbox_state.json field {key} must be a list")
                    combined = set(existing)
                    combined.difference_update(baseline - desired)
                    combined.update(desired - baseline)
                    merged[key] = sorted(combined)
                target = record_files.safe_target(root, self._path.name)
                atomic_write(target, json.dumps(merged, indent=2), mode=0o600)
            self.last_read_ts = merged.get("last_read_ts", {})
            self.channel_names = merged.get("channel_names", {})
            self.dismissed = set(merged.get("dismissed", []))
            self.muted_threads = set(merged.get("muted_threads", []))
            self.user_resolver.load(merged.get("user_cache", {}))
            self._baseline = self._snapshot()
        except OSError:
            logger.warning("Failed to save inbox state")

    def prune_dismissed(self, retention_hours: float = 168.0) -> int:
        """Remove dismissed IDs older than retention_hours."""
        cutoff = time.time() - (retention_hours * 3600)
        stale = set()
        for did in self.dismissed:
            parts = did.rsplit("_", 1)
            try:
                if float(parts[-1]) < cutoff:
                    stale.add(did)
            except (ValueError, IndexError):
                stale.add(did)
        self.dismissed -= stale
        return len(stale)


class InboxStore:
    """Persists InboxItems to disk."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_dir() / _ITEMS_FILE)
        self.items: dict[str, InboxItem] = {}
        self._dirty = False
        self._baseline_items: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self.items.clear()
                for d in data.get("items", []):
                    item = InboxItem.from_dict(d)
                    self.items[item.id] = item
                self._baseline_items = {
                    item.id: item.to_dict() for item in self.items.values()
                }
                self._dirty = False
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load inbox items, starting fresh")

    def save(self) -> None:
        try:
            with record_files.locked_store(self._path.parent) as root:
                document = _read_record_document(self._path, "items")
                if any(
                    not isinstance(row.get("id"), str) or not row["id"]
                    for row in document["items"]
                ):
                    raise ValueError("inbox.json contains an item without a valid id")
                live = {
                    row["id"]: row for row in document["items"]
                }
                current = {item.id: item.to_dict() for item in self.items.values()}
                merged_items = _merge_record_changes(
                    live, self._baseline_items, current
                )
                document["items"] = list(merged_items.values())
                target = record_files.safe_target(root, self._path.name)
                atomic_write(target, json.dumps(document, indent=2), mode=0o600)
            self._dirty = False
            self._baseline_items = {
                identity: dict(row) for identity, row in merged_items.items()
            }
            self.items = {
                identity: InboxItem.from_dict(row)
                for identity, row in merged_items.items()
            }
        except OSError:
            logger.warning("Failed to save inbox items")

    def add(self, item: InboxItem) -> None:
        if not item.owner:
            item.owner = owner_username()
        self.items[item.id] = item
        self._dirty = True

    def flush(self) -> None:
        """Save to disk if there are pending changes."""
        if self._dirty:
            self.save()

    def update(self, item_id: str, **kwargs: Any) -> InboxItem | None:
        """Apply field updates and persist. Raises :class:`InboxFieldTypeError` on a
        wrong-typed value, having written nothing.

        Validation is a separate pass over ALL of *kwargs* before the first `setattr`, so a
        two-field write with one bad field is refused whole rather than half-applied. The
        old single loop reached `self.save()` with the bad value already on the item.
        """
        item = self.items.get(item_id)
        if not item:
            return None
        validate_updatable_fields(kwargs)
        for k, v in kwargs.items():
            if k == "status":
                item.set_status_for(owner_username(), v)
                continue
            if hasattr(item, k):
                setattr(item, k, v)
        self.save()
        return item

    def apply_sort_batch(self, expected: dict[str, dict], changes: dict[str, dict]) -> list[InboxItem]:
        """Compare complete sent-row snapshots and apply verdicts under one file lock.

        A second process's human edit, removal or status move wins over a late
        model answer. No stale in-memory verdict is merged over the current disk row.
        """
        allowed = {"classification", "confidence", "classified_by", "classify_error"}
        for fields in changes.values():
            if set(fields) - allowed:
                raise ValueError("sort batch may only update machine verdict fields")
            validate_updatable_fields(fields)
        changed = []
        with record_files.locked_store(self._path.parent) as root:
            document = _read_record_document(self._path, "items")
            live = {row["id"]: row for row in document["items"]}
            for identity, fields in changes.items():
                raw = live.get(identity)
                if raw is None:
                    continue
                row = InboxItem.from_dict(raw)
                if row.to_dict() != expected.get(identity):
                    continue
                for key, value in fields.items():
                    setattr(row, key, value)
                live[identity] = row.to_dict()
                changed.append(row)
            if changed:
                document["items"] = list(live.values())
                target = record_files.safe_target(root, self._path.name)
                atomic_write(target, json.dumps(document, indent=2), mode=0o600)
        self.items = {identity: InboxItem.from_dict(row) for identity, row in live.items()}
        self._baseline_items = {identity: item.to_dict() for identity, item in self.items.items()}
        self._dirty = False
        return changed

    def pending(self, owner: str = "") -> list[InboxItem]:
        return [
            i
            for i in self.items.values()
            if i.belongs_to(owner) and i.status_for(owner) == ItemStatus.PENDING
        ]

    def open_items(self, owner: str = "") -> list[InboxItem]:
        """Every item still awaiting a decision."""
        return [
            i
            for i in self.items.values()
            if i.belongs_to(owner) and is_open_status(i.status_for(owner))
        ]

    def update_status(
        self, item_id: str, status: str, *, owner: str = ""
    ) -> InboxItem | None:
        """Persist a status transition in the requesting owner's shared-inbox state."""
        item = self.items.get(item_id)
        if item is None:
            return None
        item.set_status_for(owner, status)
        self.save()
        return item

    def cleanup_by_retention(self, retention_days: int = 90) -> int:
        """Delete items older than *retention_days*, regardless of status.

        The single inbox retention mechanism (source-agnostic — items from the
        native push sink, poll providers, and digests age out uniformly). Runs
        from the InboxService maintenance loop when auto-cleanup is enabled.
        """
        cutoff = time.time() - (retention_days * 86400)
        expired = [
            item_id for item_id, item in self.items.items() if item.created_at < cutoff
        ]
        for item_id in expired:
            del self.items[item_id]
        if expired:
            self.save()
            logger.info("Inbox auto-cleanup: deleted %d expired items", len(expired))
        return len(expired)


def evaluate_alert(item: InboxItem, user_name: str = "") -> str:
    """Why *item* deserves an immediate notification, or "" if it doesn't.

    Now reads the ``inbox/alert`` notification RULE's conditions rather than the retired
    ``alert_keywords``/``alert_on_name_mention`` inbox fields (plan 42 S3). The matching
    semantics are unchanged — `Conditions.matches` was lifted from this function's own
    body — so a user whose keywords were backfilled sees identical behavior; what changed
    is that the same conditions are now expressible for every notification kind, not just
    inbox messages.

    The ``settings`` parameter is gone rather than kept and ignored: a caller still passing
    a dict of retired fields would silently get no alerts, which is exactly the failure a
    clean break is supposed to make impossible.
    """
    text = item.message or ""
    if not text.strip():
        return ""
    try:
        from gideon.workspace import notification_rules

        rule = notification_rules.resolve_rule("inbox", "alert")
    except Exception:
        logger.debug("alert rule resolution failed", exc_info=True)
        return ""
    return rule.conditions.matches(text, user_name)


FEEDBACK_TARGETS = {"inbox_classification": "classification", "inbox_draft": "draft", "inbox_digest": "digest"}


def judgment_producers(item: dict) -> dict[str, dict]:
    """Only the stored maker can receive feedback for a machine judgment."""
    producers = {}
    for field, maker in (("classification", "classified_by"), ("draft", "drafted_by")):
        if item.get(field) and item.get(maker):
            producers[field] = {"producer_kind": "prompt", "producer_id": item[maker]}
    if item.get("source") == "digest" and item.get("classified_by"):
        producers["digest"] = {"producer_kind": "prompt", "producer_id": item["classified_by"]}
    return producers


def redact_item(item: dict) -> dict:
    """Redact LLM-generated fields, and stamp the feedback-producer meta, on one item dict.

    **Lives here, below the HTTP surface, because it is not an HTTP concern.** It moved down from
    `dashboard/handlers_inbox` when PA-3's `inbox-op` provider needed it: an auto-executed archive
    has to push the mutated row over the websocket, and every OTHER writer of that same event
    redacts first, so the provider importing the handler module would have been a core→HTTP edge
    (caught by `structural-import-direction`) *and* the alternative — a second redaction path —
    would have been the R18 duplicate that eventually diverges. `handlers_inbox._redact_item` is
    now an alias for this function, so there is exactly one implementation.
    """
    for key in ("message", "draft", "text", "context_summary"):
        if item.get(key):
            item[key], _ = redact_exfiltration_urls(item[key])
            item[key], _ = redact_credentials(item[key])
    for ctx in item.get("thread_context", []):
        if ctx.get("text"):
            ctx["text"], _ = redact_exfiltration_urls(ctx["text"])
            ctx["text"], _ = redact_credentials(ctx["text"])
    producers = judgment_producers(item)
    if producers:
        item["feedback_producers"] = producers
    else:
        item.pop("feedback_producers", None)
    return item


def live_store(state: Any) -> "InboxStore | None":
    """The RUNNING inbox service's store, or None when no service is up.

    **Every writer must go through this.** The service holds its items in MEMORY and never
    re-reads the file, so a writer that constructs its own `InboxStore()` writes a row the API
    cannot see (`_get_inbox` serves the service's instance) and that the service's next save
    silently overwrites. Found twice while wiring workflow gates: once raising a row that never
    appeared, once resolving a row that stayed open after its gate was answered.

    Type-checked, not duck-typed: a test's `MagicMock()` state answers every getattr, so an
    attribute check alone would route real writes into a mock and the row would vanish. An
    isinstance is the only thing that distinguishes a live store from an obliging fake.
    """
    svc = getattr(state, "_inbox_svc", None)
    live = getattr(svc, "inbox", None) if svc is not None else None
    return live if isinstance(live, InboxStore) else None


def live_state(state: Any) -> "InboxState | None":
    """The RUNNING inbox service's `InboxState`, or None when no service is up.

    :func:`live_store`'s sibling, for the two sets that live beside the items rather than on
    them — ``dismissed`` and ``muted_threads``. Same hazard and the same reason: the service
    holds them in memory and its next ``save()`` writes its own copy, so a writer that
    constructed its own ``InboxState()`` would add a mute the API cannot see and that the
    service then erases. Type-checked for the same reason too — a ``MagicMock()`` state answers
    every getattr, so an attribute check alone would route real writes into a fake.
    """
    svc = getattr(state, "_inbox_svc", None)
    live = getattr(svc, "state", None) if svc is not None else None
    return live if isinstance(live, InboxState) else None


def emit_attention_item(
    state: Any,
    *,
    source: str,
    kind: str,
    title: str,
    body: str = "",
    refs: dict | None = None,
    item_kind: str = "",
    store: "InboxStore | None" = None,
    dedup_key: str = "",
    addressee: str | None = None,
) -> str:
    """Raise a standing attention item AND deliver one notification for it.

    **The only correct way to raise a durable agent request.** A caller that did
    ``store.add(...)`` and ``state.notify(...)`` separately would drift the two apart — the
    common failure being two notifications for one event, or an inbox row with no delivery
    at all. Routing both through here means the notification is a *view* of the item.

    ``source``/``kind`` are the registered notification pair (delivery policy, S1);
    ``item_kind`` is the inbox row's own type and defaults to ``kind`` since for the
    attention kinds they coincide (``needs_input`` is both).

    ``dedup_key`` makes re-emission idempotent: a loop that re-checks every 30s must not
    stack a hundred identical rows. When supplied, an existing PENDING/SEEN item with the
    same key is returned untouched and **no second notification fires** — the user was
    already told.

    Returns the inbox item id ("" only if the store could not be reached, which is logged;
    a failure to persist must not also lose the notification, so delivery still happens).
    """
    resolved_kind = item_kind or kind
    target = store or live_store(state)
    if target is None:
        target = InboxStore()
        try:
            target.load()
        except Exception:  # pragma: no cover - load() already swallows OSError
            logger.warning("attention item: inbox load failed", exc_info=True)

    if dedup_key:
        existing = _find_open_by_dedup(target, dedup_key)
        if existing is not None:
            logger.debug("attention item deduped on %r → %s", dedup_key, existing.id)
            return existing.id

    now = time.time()
    item = InboxItem(
        id=make_item_id(resolved_kind, now=now),
        channel=source,
        channel_name=source,
        thread_ts=None,
        message="\n\n".join(p for p in (title, body) if p),
        sender_id=source,
        sender_name=source,
        created_at=now,
        source=source,
        can_reply=False,
        classification="",
        confidence="",
        item_kind=resolved_kind,
        refs=dict(refs or {}),
    )
    if dedup_key:
        item.refs["dedup_key"] = dedup_key
    item.owner = owner_username() if addressee is None else str(addressee)

    checking = _verification_opted_in(source, kind)
    notice_kind = notification_kinds.kind_for_legacy_pair(source, kind)
    pending = {
        "kind": notice_kind,
        "title": title,
        "body": body,
        "item_kind": resolved_kind,
    }
    if checking:
        item.refs["verify"] = "checking"
        item.refs["verify_pending"] = pending

    item_id = ""
    try:
        target.add(item)
        target.flush()
        item_id = item.id
    except Exception:
        logger.warning("attention item: inbox write failed", exc_info=True)

    if state is not None and item_id:
        try:
            state.broadcast_ws("inbox_new_item", redact_item(item.to_dict()))
        except Exception:
            logger.debug("attention item: inbox row broadcast failed", exc_info=True)

    if checking and item_id:
        _schedule_attention_verification(state, target, item, pending)
    elif state is not None:
        try:
            state.notify(
                notice_kind,
                title,
                body,
                meta={
                    "inbox_item": item_id,
                    "item_kind": resolved_kind,
                    "addressee": item.owner,
                    **dict(refs or {}),
                },
            )
        except Exception:
            logger.warning("attention item: notify failed", exc_info=True)
    return item_id


def _schedule_attention_verification(
    state: Any, store: InboxStore, item: InboxItem, pending: dict[str, Any]
) -> None:
    """Run the managed check off the caller and apply its result on the owner loop."""
    try:
        caller_loop = asyncio.get_running_loop()
    except RuntimeError:
        caller_loop = None
    owner_loop = getattr(state, "_ws_loop", None)
    if owner_loop is None:
        owner_loop = caller_loop
    elif owner_loop.is_closed():
        owner_loop = None

    async def check() -> None:
        from gideon.workspace.notification_verify import verify_attention_item

        verdict = await verify_attention_item(
            str(pending.get("title") or ""), str(pending.get("body") or "")
        )
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        apply = lambda: _apply_attention_verdict(
            state, store, item.id, verdict, pending
        )
        if owner_loop is not None and running is not owner_loop:
            if not owner_loop.is_closed():
                owner_loop.call_soon_threadsafe(apply)
            return
        apply()

    if owner_loop is not None:
        def create_and_track() -> None:
            if owner_loop.is_closed() or not owner_loop.is_running():
                return
            coroutine = check()
            try:
                task = owner_loop.create_task(coroutine)
            except RuntimeError:
                coroutine.close()
                return
            with _verification_tasks_lock:
                _verification_tasks.add(task)

            def forget(done: asyncio.Task) -> None:
                with _verification_tasks_lock:
                    _verification_tasks.discard(done)

            task.add_done_callback(forget)

        if caller_loop is owner_loop:
            create_and_track()
        else:
            try:
                owner_loop.call_soon_threadsafe(create_and_track)
            except RuntimeError:
                _start_attention_verification_worker(check, state, store, item, pending)
        return

    _start_attention_verification_worker(check, state, store, item, pending)


def _start_attention_verification_worker(
    check: Any, state: Any, store: InboxStore, item: InboxItem, pending: dict[str, Any]
) -> None:
    def worker() -> None:
        try:
            asyncio.run(check())
        except Exception:
            logger.debug("attention verification worker failed", exc_info=True)
            _apply_attention_verdict(state, store, item.id, "skipped", pending)

    threading.Thread(target=worker, name="inbox-verification", daemon=True).start()


def _apply_attention_verdict(
    state: Any,
    store: InboxStore,
    item_id: str,
    verdict: str,
    pending: dict[str, Any],
) -> None:
    item = store.items.get(item_id)
    if item is None or item.refs.get("verify") != "checking":
        return
    item.refs["verify"] = verdict
    item.refs.pop("verify_pending", None)
    untouched = item.status_for(item.owner) == ItemStatus.PENDING.value
    withheld = verdict == "refuted" and untouched
    if withheld:
        item.set_status_for(item.owner, ItemStatus.FILTERED.value)
        item.refs["verify_withheld"] = dict(pending)
    store.save()
    if state is not None:
        try:
            state.broadcast_ws("inbox_item_updated", redact_item(item.to_dict()))
        except Exception:
            logger.debug("attention verification broadcast failed", exc_info=True)
    if not untouched or withheld or state is None:
        return
    try:
        state.notify(
            str(pending.get("kind") or item.source),
            str(pending.get("title") or ""),
            str(pending.get("body") or ""),
            meta={"inbox_item": item.id, "item_kind": item.item_kind, **dict(item.refs)},
        )
    except Exception:
        logger.warning("attention verification: notify failed", exc_info=True)


def settle_verification_rows(state: Any, store: InboxStore) -> int:
    """Fail open checks interrupted by restart and repair stale filtered decisions."""
    changed = 0
    for item in store.items.values():
        if item.refs.get("verify") == "checking":
            notice = item.refs.pop("verify_pending", None)
            legacy = item.refs.pop("verify_withheld", None)
            if not isinstance(notice, dict):
                notice = legacy if isinstance(legacy, dict) else {}
            item.refs["verify"] = "skipped"
            changed += 1
            if state is not None and item.status_for(item.owner) == ItemStatus.PENDING.value:
                try:
                    state.notify(
                        str(notice.get("kind") or item.source),
                        str(notice.get("title") or ""),
                        str(notice.get("body") or ""),
                        meta={"inbox_item": item.id, "item_kind": item.item_kind},
                    )
                except Exception:
                    logger.debug("interrupted attention notification failed", exc_info=True)
        elif item.status_for(item.owner) == ItemStatus.FILTERED.value and isinstance(item.refs.get("verify_withheld"), dict):
            withheld = item.refs["verify_withheld"]
            is_decision = item.item_kind == ItemKind.AGENT_REQUEST.value or bool(item.refs.get("approval"))
            if not is_decision:
                continue
            approval_id = str(item.refs.get("approval") or "")
            pending_approvals = getattr(state, "_pending_approvals", {}) if state is not None else {}
            if approval_id and approval_id not in pending_approvals:
                item.set_status_for(item.owner, ItemStatus.HANDLED.value)
                item.refs.pop("verify_withheld", None)
                changed += 1
                continue
            item.set_status_for(item.owner, ItemStatus.PENDING.value)
            item.refs.pop("verify_withheld", None)
            changed += 1
            if state is not None:
                try:
                    state.notify(
                        str(withheld.get("kind") or item.source),
                        str(withheld.get("title") or ""),
                        str(withheld.get("body") or ""),
                        meta={"inbox_item": item.id, "item_kind": item.item_kind},
                    )
                except Exception:
                    logger.debug("legacy decision notification failed", exc_info=True)
    if changed:
        store.save()
    return changed


def emit_shared_knowledge_item(
    state: Any,
    provider: Any,
    item: Any,
    *,
    store: "InboxStore | None" = None,
) -> str:
    """Queue one foreign-authored, explicitly shared provider knowledge item."""
    metadata = (
        item.metadata if isinstance(getattr(item, "metadata", None), dict) else {}
    )
    author = str(metadata.get("owner_username") or "").strip().lower()
    owner = owner_username().strip().lower()
    if (
        metadata.get("sharing_policy") != "shared"
        or not author
        or (owner and author == owner)
    ):
        return ""
    provider_name = str(getattr(provider, "name", "") or "knowledge")
    item_id = str(getattr(item, "id", "") or "")
    title = str(getattr(item, "title", "") or "Shared knowledge")
    content = str(getattr(item, "content", "") or "")
    return emit_attention_item(
        state,
        source="knowledge",
        kind="research_finding",
        title=f"{author} shared: {title}",
        body=content,
        refs={
            "knowledge_item": item_id,
            "provider": provider_name,
            "shared_by": author,
        },
        item_kind=ItemKind.SYSTEM.value,
        store=store,
        dedup_key=f"shared-knowledge:{provider_name}:{item_id}",
    )


def _verification_opted_in(source: str, kind: str) -> bool:
    """True only when *kind* is a verifiable registration AND its rule set ``verify:true``.

    Fail-CLOSED to False (deliver without verifying) on any error: a broken policy read must
    never *start* filtering notifications that would otherwise be delivered. The registry
    check runs first so the common non-verifiable path never touches the rules store.
    """
    try:
        registered = notification_kinds.resolve_kind(source, kind)
        if not registered.verifiable or registered.decision:
            return False
        from gideon.workspace import notification_rules

        rule = notification_rules.resolve_rule(source, kind)
        return bool(getattr(rule, "verify", False))
    except Exception:
        logger.debug("verify opt-in check failed — not verifying", exc_info=True)
        return False


def resolve_attention_items(
    state: Any, refs: dict[str, str], *, store: "InboxStore | None" = None
) -> int:
    """Close every open attention row whose ``refs`` match ALL the given pairs. Returns the count.

    The counterpart to :func:`emit_attention_item`, and deliberately its neighbour: a durable
    row raised for a standing request has to be closed when the request stops standing, and a
    surface that emits without resolving teaches the user to distrust the inbox — they open the
    row, find nothing to do, and stop looking. Both halves belong to whoever owns the seam, so
    both live here rather than the resolve half being re-implemented per caller.

    Matching on a REF SUBSET is what lets one implementation serve every emitter's own ref
    vocabulary: a workflow gate resolves on ``{"workflow": run, "workflow_node": node}``, a loop
    on ``{"loop": loop_id}``, and a future surface on whatever it stamped. Every pair must match,
    so a scoped resolve cannot close a sibling row (a run with two concurrent gates has two rows,
    and answering one must not answer the other).

    ``HANDLED``, not ``DISMISSED``: the request was actually answered — by the user, or by the
    engine on their behalf when the cause went away. "Dismissed" reads as *ignored*, which is a
    different fact and feeds the engagement signals differently.

    An EMPTY ``refs`` closes nothing and says so. It would otherwise match every row and empty
    the user's inbox, and the one thing a caller can easily get wrong here is passing a dict
    whose values were all falsy.

    Best-effort like every other attention write: whatever the caller was doing (resuming a
    loop, ending a run) matters more than the bookkeeping, and must not fail because of it.
    """
    if not refs or not all(refs.values()):
        logger.debug("resolve_attention_items: refusing an unscoped resolve (%r)", refs)
        return 0
    try:
        target = store or live_store(state)
        if target is None:
            target = InboxStore()
            target.load()
        closed = 0
        for item in list(target.items.values()):
            if not is_open_status(item.status_for(item.owner)):
                continue
            if any(item.refs.get(key) != value for key, value in refs.items()):
                continue
            item.set_status_for(item.owner, ItemStatus.HANDLED.value)
            closed += 1
        if closed:
            target.save()
        return closed
    except Exception:
        logger.debug("could not resolve the attention rows for %r", refs, exc_info=True)
        return 0


def _find_open_by_dedup(store: "InboxStore", dedup_key: str) -> "InboxItem | None":
    """An unresolved item carrying ``dedup_key``, newest first.

    Only PENDING/SEEN count as open: once the user has HANDLED or DISMISSED a request, a
    later re-emission is genuinely new and should surface again rather than be swallowed.
    """
    matches = [
        i
        for i in store.items.values()
        if i.refs.get("dedup_key") == dedup_key
        and is_open_status(i.status_for(i.owner))
    ]
    if not matches:
        return None
    return max(matches, key=lambda i: i.created_at)


def notify_inbox_alert(state: Any, item: InboxItem, reason: str) -> None:
    """Fire a dashboard notification for an alert-worthy inbox item.

    Message text is external/untrusted — redacted before it enters the
    notification feed (same treatment as the inbox item handlers)."""
    if state is None:
        return
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    msg, _ = redact_exfiltration_urls(item.message)
    msg, _ = redact_credentials(msg)
    state.notify(
        notification_kinds.INBOX_ALERT,
        f"{item.sender_name} in {item.channel_name}",
        f"Alert ({reason}): {msg[:200]}",
        meta={"session": f"inbox:{item.id}"},
    )

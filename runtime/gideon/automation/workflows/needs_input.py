from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gideon.integrations.inbox import is_open_status

RENOTIFY_AFTER_HOURS = 24
MAX_RENOTIFICATIONS = 1
MAX_CHOICES = 5
MAX_EVIDENCE_CHARS = 600


class BlockKind(str, Enum):
    NEEDS_INPUT = "needs_input"
    CAPABILITY = "capability"
    TRANSIENT = "transient"
    APPROVAL = "approval"


_CAPABILITY_CLASSES = frozenset({"permission", "budget"})
_TRANSIENT_CLASSES = frozenset({"transient", "network", "timeout"})
USER_ACTIONABLE = frozenset(
    {BlockKind.NEEDS_INPUT, BlockKind.CAPABILITY, BlockKind.APPROVAL}
)


@dataclass
class NeedsInputItem:
    run_id: str
    node_id: str
    block_kind: BlockKind = BlockKind.NEEDS_INPUT
    blocker: str = ""
    attempted: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    choices: list[str] = field(default_factory=list)
    resume_token: str = ""
    owner: str = ""
    project_id: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    renotifications: int = 0

    @property
    def actionable(self) -> bool:
        return self.block_kind in USER_ACTIONABLE

    def to_dict(self) -> dict[str, Any]:
        identity = dict(
            run_id=self.run_id,
            node_id=self.node_id,
            block_kind=self.block_kind.value,
            blocker=self.blocker,
            attempted=list(self.attempted),
            evidence=dict(self.evidence),
        )
        names = (
            "recommendation",
            "choices",
            "resume_token",
            "owner",
            "project_id",
            "created_at",
            "expires_at",
            "renotifications",
            "actionable",
        )
        for name in names:
            value = getattr(self, name)
            identity[name] = list(value) if name == "choices" else value
        return identity

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NeedsInputItem:
        data = d or {}
        try:
            kind = BlockKind(str(data.get("block_kind", "") or "needs_input"))
        except ValueError:
            kind = BlockKind.NEEDS_INPUT
        return cls(**_CardFields(data).decode(kind))


class _CardFields:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def decode(self, kind: BlockKind) -> dict[str, Any]:
        strings = {
            "run_id",
            "node_id",
            "blocker",
            "recommendation",
            "resume_token",
            "owner",
            "project_id",
        }
        sequences = {"attempted", "choices"}
        fields = (
            "run_id",
            "node_id",
            "block_kind",
            "blocker",
            "attempted",
            "evidence",
            "recommendation",
            "choices",
            "resume_token",
            "owner",
            "project_id",
            "created_at",
            "expires_at",
            "renotifications",
        )
        result: dict[str, Any] = {}
        for name in fields:
            if name in strings:
                result[name] = str(self.data.get(name, "") or "")
            elif name in sequences:
                result[name] = list(map(str, self.data.get(name) or []))
            elif name == "block_kind":
                result[name] = kind
            elif name == "evidence":
                result[name] = dict(self.data.get(name) or {})
            elif name == "renotifications":
                result[name] = int(self.data.get(name, 0) or 0)
            else:
                result[name] = float(self.data.get(name, 0.0) or 0.0)
        return result


class _CardAssembly:
    def __init__(
        self, ask: dict[str, Any] | None, failure: dict[str, Any] | None
    ) -> None:
        self.ask, self.failure = ask or {}, failure

    def describe(
        self,
        node_id: str,
        attempts: list[dict[str, Any]] | None,
        evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kind = classify_block(self.ask, self.failure)
        fields: dict = dict(
            block_kind=kind, blocker=_blocker_text(self.ask, self.failure, node_id)
        )
        fields["attempted"] = summarize_attempts(attempts)
        fields["evidence"] = trim_evidence(evidence)
        fields["recommendation"] = _recommendation(self.ask, self.failure, kind)
        fields["choices"] = list(map(str, self.ask.get("choices") or []))[:MAX_CHOICES]
        return fields


def build_item(
    *,
    run_id: str,
    node_id: str,
    ask: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    resume_token: str = "",
    owner: str = "",
    project_id: str = "",
    now: float = 0.0,
    ttl_secs: float = 0.0,
) -> NeedsInputItem:
    fields = _CardAssembly(ask, failure).describe(node_id, attempts, evidence)
    fields.update(
        run_id=run_id,
        node_id=node_id,
        resume_token=resume_token,
        owner=owner,
        project_id=project_id,
        created_at=now,
        expires_at=now + ttl_secs if now and ttl_secs else 0.0,
    )
    return NeedsInputItem(**fields)


def classify_block(
    ask: dict[str, Any] | None, failure: dict[str, Any] | None
) -> BlockKind:
    if str((ask or {}).get("kind") or "").strip().lower() == "approval":
        return BlockKind.APPROVAL
    failure_class = str((failure or {}).get("failure_class") or "").strip().lower()
    for classes, result in (
        (_CAPABILITY_CLASSES, BlockKind.CAPABILITY),
        (_TRANSIENT_CLASSES, BlockKind.TRANSIENT),
    ):
        if failure_class in classes:
            return result
    return BlockKind.NEEDS_INPUT


def _blocker_text(
    ask: dict[str, Any], failure: dict[str, Any] | None, node_id: str
) -> str:
    for source, key in ((ask, "prompt"), (failure, "cause_plain")):
        text = str((source or {}).get(key) or "").strip()
        if text:
            return text
    return f"`{node_id or 'a step'}` is waiting"


def _recommendation(
    ask: dict[str, Any], failure: dict[str, Any] | None, kind: BlockKind
) -> str:
    suggested = ask.get("default")
    if suggested not in (None, ""):
        return f"Recommended: {suggested}"
    repair = str((failure or {}).get("remediation") or "").strip()
    if repair:
        return repair
    return (
        "Review the output above, then approve or reject."
        if kind is BlockKind.APPROVAL
        else ""
    )


def _attempt_line(attempt: dict[str, Any]) -> str:
    number = attempt.get("attempt")
    outcome = str(attempt.get("outcome") or "").strip() or "unknown"
    note = str(attempt.get("note") or attempt.get("cause_plain") or "").strip()
    label = outcome if number is None else f"attempt {number}: {outcome}"
    return " — ".join((label, note)) if note else label


def summarize_attempts(attempts: list[dict[str, Any]] | None) -> list[str]:
    return [
        _attempt_line(attempt)
        for attempt in attempts or []
        if isinstance(attempt, dict)
    ]


def _evidence_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_EVIDENCE_CHARS]
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, list):
        return [str(entry)[:200] for entry in value[:10]]
    return str(value)[:MAX_EVIDENCE_CHARS]


def trim_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    result = {}
    for key, value in (evidence or {}).items():
        projected = _evidence_value(value)
        result[str(key)] = projected
    return result


def may_satisfy(item: NeedsInputItem, session_key: str) -> tuple[bool, str]:
    denied = bool(item.owner) and session_key != item.owner
    return (
        (False, f"only the requesting session ({item.owner}) may answer this")
        if denied
        else (True, "")
    )


class _ReminderDecision:
    def __init__(self, item: NeedsInputItem, status: str) -> None:
        self.item, self.status = item, status

    def suppression(self) -> str | None:
        rules = (
            (
                lambda: not is_open_status(self.status),
                lambda: f"card is {self.status}",
            ),
            (
                lambda: self.item.renotifications >= MAX_RENOTIFICATIONS,
                lambda: "already reminded once — further reminders train the user to mute",
            ),
            (
                lambda: not self.item.actionable,
                lambda: f"{self.item.block_kind.value} blocks are not the user's to answer",
            ),
            (lambda: not self.item.created_at, lambda: "no creation time recorded"),
        )
        for blocked, explanation in rules:
            if blocked():
                return explanation()
        return None

    def evaluate(self, now: float) -> tuple[bool, str]:
        reason = self.suppression()
        if reason is not None:
            return False, reason
        hours = (now - self.item.created_at) / 3600.0
        if hours < RENOTIFY_AFTER_HOURS:
            return False, f"{hours:.1f}h old (reminder at {RENOTIFY_AFTER_HOURS}h)"
        return True, f"unanswered for {hours:.0f}h"


def should_renotify(
    item: NeedsInputItem, *, now: float, status: str = "pending"
) -> tuple[bool, str]:
    return _ReminderDecision(item, status).evaluate(now)


def renotify_text(item: NeedsInputItem) -> str:
    return "Still waiting: " + item.blocker[:100]


def marked_renotified(item: NeedsInputItem) -> NeedsInputItem:
    values = dict(item.__dict__)
    values["renotifications"] = item.renotifications + 1
    return NeedsInputItem(**values)


def expired(item: NeedsInputItem, *, now: float) -> bool:
    return bool(item.expires_at) and now >= item.expires_at


def card_refs(item: NeedsInputItem) -> dict[str, Any]:
    keys = ("workflow", "workflow_node", "resume_token", "needs_input")
    return dict(
        zip(keys, (item.run_id, item.node_id, item.resume_token, item.to_dict()))
    )


def from_refs(refs: dict[str, Any] | None) -> NeedsInputItem | None:
    payload = (refs or {}).get("needs_input")
    if isinstance(payload, dict) and payload:
        return NeedsInputItem.from_dict(payload)
    return None


def one_decision_lint(item: NeedsInputItem) -> list[str]:
    findings = []
    choices = len(item.choices)
    if choices > MAX_CHOICES:
        findings += [
            f"{choices} choices exceeds {MAX_CHOICES} — an inbox row is a glance, and a "
            "menu this long is read as a wall"
        ]
    questions = item.blocker.count("?")
    if questions > 1:
        findings += [
            f"the blocker asks {questions} questions — one decision per card, "
            "or the run stays blocked on the ones the user did not notice"
        ]
    if item.block_kind is BlockKind.APPROVAL and item.choices:
        findings += [
            "an approval card with explicit choices is two affordances for one decision; approve/"
            "reject is the affordance"
        ]
    return findings

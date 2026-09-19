from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

DEFAULT_TTL_SECS = 7 * 24 * 3600
MAX_PREVIEW_CHARS = 400


class ConfirmationType(str, Enum):
    APPROVAL = "approval"
    NEEDS_INPUT = "needs_input"
    DESTRUCTIVE_CONFIRM = "destructive_confirm"


class Status(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class ExpiryPolicy(str, Enum):
    AUTO_REJECT = "auto_reject"
    HOLD = "hold"


EXPIRY_POLICY: dict[ConfirmationType, ExpiryPolicy] = {
    ConfirmationType.APPROVAL: ExpiryPolicy.HOLD,
    ConfirmationType.NEEDS_INPUT: ExpiryPolicy.HOLD,
    ConfirmationType.DESTRUCTIVE_CONFIRM: ExpiryPolicy.AUTO_REJECT,
}
RESOLUTIONS = ("approve", "reject", "skip", "quit")
MUTABLE_TYPES = frozenset({ConfirmationType.APPROVAL, ConfirmationType.NEEDS_INPUT})
TOOL_PROFILES: dict[str, dict[str, Any]] = {
    "read_only": {
        "capability": "research",
        "confirm": (),
        "note": "reads only; no write tool is reachable",
    },
    "write_local": {
        "capability": "mutating",
        "confirm": (ConfirmationType.APPROVAL,),
        "note": "may write in its own workspace; an approval gate precedes the first write",
    },
    "outward": {
        "capability": "mutating",
        "confirm": (ConfirmationType.APPROVAL, ConfirmationType.DESTRUCTIVE_CONFIRM),
        "note": "may publish or send; every outward action is confirmed",
    },
}


@dataclass
class ConfirmationRequest:
    id: str
    run_id: str
    gate_id: str
    type: ConfirmationType = ConfirmationType.APPROVAL
    risk_category: str = ""
    title: str = ""
    payload_preview: str = ""
    requested_at: float = 0.0
    ttl_seconds: int = DEFAULT_TTL_SECS
    status: Status = Status.PENDING
    resolved_by: str = ""
    resolution_note: str = ""
    resume_token: str = ""

    @property
    def expiry_policy(self) -> ExpiryPolicy:
        return EXPIRY_POLICY.get(self.type, ExpiryPolicy.HOLD)

    def expires_at(self) -> float:
        return (
            self.requested_at + self.ttl_seconds
            if not (self.ttl_seconds <= 0) and self.requested_at
            else 0.0
        )

    def expired(self, now: float) -> bool:
        until = self.expires_at()
        return bool(until) and now >= until

    def to_dict(self) -> dict[str, Any]:
        return _RequestWire.encode(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConfirmationRequest:
        return cls(**_RequestWire.decode(d or {}))


class _RequestWire:
    fields = (
        "id",
        "run_id",
        "gate_id",
        "type",
        "risk_category",
        "title",
        "payload_preview",
        "requested_at",
        "ttl_seconds",
        "status",
        "resolved_by",
        "resolution_note",
        "resume_token",
    )

    @staticmethod
    def enum_value(data: dict[str, Any], name: str, enum: Any, fallback: Any) -> Any:
        try:
            return enum(str(data.get(name, "") or fallback.value))
        except ValueError:
            return fallback

    @classmethod
    def decode(cls, data: dict[str, Any]) -> dict[str, Any]:
        kind = cls.enum_value(data, "type", ConfirmationType, ConfirmationType.APPROVAL)
        status = cls.enum_value(data, "status", Status, Status.PENDING)
        decoded: dict[str, Any] = {}
        for name in cls.fields:
            if name == "type":
                value = kind
            elif name == "status":
                value = status
            elif name == "requested_at":
                value = float(data.get(name, 0.0) or 0.0)
            elif name == "ttl_seconds":
                value = int(data.get(name, DEFAULT_TTL_SECS) or 0)
            else:
                value = str(data.get(name, "") or "")
            decoded[name] = value
        return decoded

    @classmethod
    def encode(cls, request: ConfirmationRequest) -> dict[str, Any]:
        result = {}
        for name in cls.fields:
            value = getattr(request, name)
            result[name] = value.value if name in ("type", "status") else value
        result["expires_at"] = request.expires_at()
        result["expiry_policy"] = request.expiry_policy.value
        return result


def request_id(run_id: str, gate_id: str, epoch: int) -> str:
    encoded = f"{run_id}\n{gate_id}\n{epoch}".encode("utf-8")
    digest = hashlib.sha1(encoded).hexdigest()
    return f"cr-{digest[:12]}"


def redact_preview(payload: Any) -> str:
    if payload is None:
        return ""
    display = payload if isinstance(payload, str) else str(payload)
    if display.strip():
        try:
            from gideon.security.security import redact

            redacted = redact(display)
        except Exception:
            return "[preview withheld: redaction unavailable]"
        return redacted[:MAX_PREVIEW_CHARS]
    return ""


def build_request(
    *,
    run_id: str,
    gate_id: str,
    epoch: int = 0,
    kind: ConfirmationType = ConfirmationType.APPROVAL,
    title: str = "",
    payload: Any = "",
    risk_category: str = "",
    resume_token: str = "",
    now: float = 0.0,
    ttl_seconds: int | None = None,
) -> ConfirmationRequest:
    if ttl_seconds is None:
        from gideon.automation.workflows.settings import confirmation_ttl_secs

        ttl_seconds = confirmation_ttl_secs()
    identity = request_id(run_id, gate_id, epoch)
    description = title or f"{gate_id} needs your decision"
    preview = redact_preview(payload)
    fields: dict = dict(
        id=identity,
        run_id=run_id,
        gate_id=gate_id,
        type=kind,
        risk_category=risk_category,
        title=description,
        payload_preview=preview,
        requested_at=now,
        ttl_seconds=ttl_seconds,
        resume_token=resume_token,
    )
    return ConfirmationRequest(**fields)


@dataclass
class Resolution:
    verb: str
    approved: bool = False
    resumes: bool = False
    still_pending: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in ("verb", "approved", "resumes", "still_pending", "note")
        }


def resolve(verb: str, *, note: str = "") -> tuple[Resolution | None, str]:
    selected = (verb or "").strip().lower()
    if selected in RESOLUTIONS:
        flags = {
            "approve": (True, True, False),
            "reject": (False, True, False),
            "skip": (False, False, True),
        }.get(selected, (False, False, False))
        approved, resumes, pending = flags
        return (
            Resolution(
                verb=selected,
                approved=approved,
                resumes=resumes,
                still_pending=pending,
                note=note,
            ),
            "",
        )
    return (
        None,
        f"unknown resolution {selected or '(empty)'!r}; expected one of {list(RESOLUTIONS)}",
    )


class _ExpiryDecision:
    def __init__(self, request: ConfirmationRequest) -> None:
        self.request = request

    def at(self, now: float) -> tuple[Status, Resolution | None, str]:
        if self.request.expired(now):
            rejecting = self.request.expiry_policy is ExpiryPolicy.AUTO_REJECT
            status = Status.EXPIRED if rejecting else Status.PENDING
            answer = (
                Resolution(
                    verb="reject",
                    approved=False,
                    resumes=True,
                    note="expired unanswered",
                )
                if rejecting
                else None
            )
            text = (
                "auto-REJECTED — the action did not happen"
                if rejecting
                else "HELD — the answer is still wanted"
            )
            joining = "and was" if rejecting else "but is"
            return status, answer, f"{self.request.type.value} expired {joining} {text}"
        return self.request.status, None, ""


def on_expiry(
    request: ConfirmationRequest, now: float
) -> tuple[Status, Resolution | None, str]:
    return _ExpiryDecision(request).at(now)


def requires_hitl(node_config: dict[str, Any]) -> bool:
    return (node_config or {}).get("require_hitl") is True


def may_mute(kind: ConfirmationType) -> tuple[bool, str]:
    if kind not in MUTABLE_TYPES:
        message = (
            f"{kind.value} cannot be muted — muting the last check before an unrecoverable action is "
            "the one setting that cannot be undone by changing it back"
        )
        return False, message
    return True, ""


def profile(name: str) -> tuple[dict[str, Any] | None, str]:
    key = (name or "").strip().lower()
    record = TOOL_PROFILES.get(key)
    if record is not None:
        return dict(record), ""
    return (
        None,
        f"unknown tool profile {name!r}; expected one of {sorted(TOOL_PROFILES)}",
    )


def audit_fields(
    request: ConfirmationRequest, resolution: Resolution
) -> dict[str, Any]:
    fields: dict = dict(
        operation=f"confirmation.{resolution.verb}", confirmation_id=request.id
    )
    for name in ("run_id", "gate_id", "type", "risk_category", "resolved_by"):
        value = getattr(request, name)
        if name == "type":
            value = value.value
        elif name == "resolved_by":
            value = value or "unknown"
        fields[name] = value
    fields["approved"] = resolution.approved
    return fields


@dataclass
class DagViewCard:
    confirmation_id: str
    node_id: str
    title: str
    preview: str
    awaiting: bool
    can_approve: bool
    can_deny: bool

    def to_dict(self) -> dict[str, Any]:
        names = (
            "confirmation_id",
            "node_id",
            "title",
            "preview",
            "awaiting",
            "can_approve",
            "can_deny",
        )
        return {name: getattr(self, name) for name in names}


def dag_card(request: ConfirmationRequest) -> DagViewCard:
    live = request.status is Status.PENDING
    state: dict = dict(
        confirmation_id=request.id,
        node_id=request.gate_id,
        title=request.title,
        preview=request.payload_preview,
    )
    state.update(dict.fromkeys(("awaiting", "can_approve", "can_deny"), live))
    return DagViewCard(**state)

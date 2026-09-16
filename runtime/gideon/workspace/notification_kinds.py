"""The notification kind registry (INBOX-NOTIFICATIONS-UNIFICATION C1).

Every notification this system delivers is a ``(source, kind)`` pair, registered here
once. Before this module, ``kind`` was a bare string invented at each of 25 call sites —
so nothing could enumerate what the system is *able* to tell you, and the rules UI had
nothing to draw a row for. A registry turns "what notifications exist?" from a grep into
a function call.

**Source vs kind.** ``source`` is the emitter domain (who is speaking: ``cron``,
``loop``, ``inbox``, ``system``…); ``kind`` is what kind of thing is being said
(``needs_input``, ``proposal``, ``failed``…). They are separate because the rules layer
wants both axes: "never notify me about anything from ``heartbeat``" and "always
interrupt me for a ``needs_input``, whoever raised it" are both natural rules, and a
single flat string can express neither.

**Registration is frozen at import.** Registering the same ``(source, kind)`` twice
raises — a duplicate means two emitters disagree about what they're emitting, which is a
bug worth failing the import over rather than resolving by last-write-wins.

**Resolution is fail-OPEN.** An unregistered pair resolves to a synthetic
``(system, generic)`` kind and logs a warning instead of raising. This mirrors the
existing delivery gate's philosophy (`providers/entity_routes.notification_allowed`
delivers when its own settings file is unreadable): a notification the system could not
classify is still a notification the user should see. Losing a message because a plugin
forgot to register is worse than showing one with a generic label.

**Severity means the same thing it already did.** 1=info, 2=warning, 3=error, matching
`_KIND_SEVERITY`/`_MIN_SEVERITY_RANK` in `providers/entity_routes.py` — 3 is the rank
that bypasses quiet hours. This module does not re-implement the global gate; it supplies
the severity the gate reads.

**Every default_mode is ``immediate``, deliberately.** ``badge`` is the interesting new
capability — persist without interrupting — and heartbeats, loop progress and
signal-retirement notices are all obvious candidates for it. But this plan replaces the
delivery path outright with no gate to hide behind, so its safety property is that a user
with no rules file sees *exactly* what they see today, and today every emitter that passes
the global gate produces a toast. Shipping opinionated `badge` defaults would silently
stop delivering three kinds of notification as a side effect of a refactor — the user
would experience it as "notifications stopped working," with no setting they knowingly
changed. So the registry ships behavior-preserving defaults and `badge` becomes something
the user opts into per row in the rules matrix. `tests/test_notification_kinds.py` pins
this as an invariant, not a coincidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Mode = Literal["never", "badge", "immediate", "digest"]

MODES: tuple[str, ...] = ("never", "badge", "immediate", "digest")

SEV_INFO = 1
SEV_WARNING = 2
SEV_ERROR = 3

GENERIC_SOURCE = "system"
GENERIC_KIND = "generic"


@dataclass(frozen=True)
class NotificationKind:
    """One registered kind of notification.

    ``default_mode``/``default_severity`` are the behavior when the user has no rule for
    this pair — which is the common case, and the case the "no rules file behaves exactly
    as before" regression test pins.
    """

    source: str
    kind: str
    label: str
    default_mode: Mode = "immediate"
    default_severity: int = SEV_INFO
    attention: bool = False
    verifiable: bool = False

    @property
    def key(self) -> str:
        """The ``<source>/<kind>`` string used as the rules-store key."""
        return f"{self.source}/{self.kind}"


_REGISTRY: dict[tuple[str, str], NotificationKind] = {}


def register(k: NotificationKind) -> None:
    """Register a kind. Raises ``ValueError`` on a duplicate ``(source, kind)``."""
    ident = (k.source, k.kind)
    if ident in _REGISTRY:
        raise ValueError(f"duplicate notification kind registration: {k.key}")
    if k.default_mode not in MODES:
        raise ValueError(f"{k.key}: unknown mode {k.default_mode!r}")
    if k.default_severity not in (SEV_INFO, SEV_WARNING, SEV_ERROR):
        raise ValueError(
            f"{k.key}: severity must be 1, 2 or 3 (got {k.default_severity})"
        )
    _REGISTRY[ident] = k


def unregister(source: str, kind: str) -> bool:
    """Drop a dynamically registered kind. Returns True when one was removed.

    Only the app-contributed kinds (INU-7) use this: an app's proposal kind must not
    outlive the app that declared it, or a disabled app leaves a phantom kind in the rules
    UI and in ``resolve_kind`` — the same phantom-source failure ``deregister()`` exists to
    prevent on the provider seam. The built-in kinds are registered once at import and
    never removed.
    """
    return _REGISTRY.pop((source, kind), None) is not None


def all_kinds() -> list[NotificationKind]:
    """Every registered kind, ordered by source then kind (stable for the rules UI)."""
    return sorted(_REGISTRY.values(), key=lambda k: (k.source, k.kind))


def resolve_kind(source: str, kind: str) -> NotificationKind:
    """The registered kind, or a synthetic generic one (fail-open + warn)."""
    found = _REGISTRY.get((source, kind))
    if found is not None:
        return found
    logger.warning(
        "unregistered notification kind %s/%s — delivering as %s/%s",
        source,
        kind,
        GENERIC_SOURCE,
        GENERIC_KIND,
    )
    return NotificationKind(
        source=GENERIC_SOURCE,
        kind=GENERIC_KIND,
        label=f"{source}/{kind}" if source or kind else "Notification",
        default_mode="immediate",
        default_severity=SEV_INFO,
    )


def kind_for_legacy_pair(source: str, kind: str) -> str:
    """The flat wire string for a registered ``(source, kind)``.

    Emitters that know their typed pair (the attention kinds, which never had a legacy flat
    string) still have to hand ``notify()`` a wire value, since the flat string is what the
    persisted log and the SPA's display map key on. This is the one place that mapping
    lives, so a new attention kind cannot invent a second convention.

    Prefers an existing legacy string when one maps to this pair — so ``inbox/alert`` keeps
    emitting ``inbox_alert`` and its persisted history stays one kind — and otherwise falls
    back to the bare ``kind``, which is what a brand-new attention kind wants.
    """
    for flat, ident in _WIRE_TO_PAIR.items():
        if ident == (source, kind):
            return flat
    return kind


def kind_for_legacy(kind: str) -> NotificationKind:
    """Resolve a bare pre-registry ``kind`` string to a registered kind.

    The persisted notification log and the SSE wire both carry a flat ``kind`` string,
    and 25 call sites passed one. Rather than rewrite history or break the frontend's
    display map, the flat string stays the wire format and this function maps it back to
    its registration. Unknown → generic, fail-open.
    """
    flat = (kind or "").strip().lower()
    ident = _WIRE_TO_PAIR.get(flat)
    if ident is None:
        return resolve_kind(GENERIC_SOURCE, flat or GENERIC_KIND)
    return resolve_kind(*ident)


_KINDS: tuple[NotificationKind, ...] = (
    NotificationKind("cron", "result", "Scheduled job result", "immediate", SEV_INFO),
    NotificationKind("cron", "failed", "Scheduled job failed", "immediate", SEV_INFO),
    NotificationKind("heartbeat", "status", "Heartbeat", "immediate", SEV_INFO),
    NotificationKind("loop", "complete", "Loop complete", "immediate", SEV_INFO),
    NotificationKind("loop", "failed", "Loop failed", "immediate", SEV_ERROR),
    NotificationKind(
        "loop", "stalled", "Loop stalled or blocked", "immediate", SEV_WARNING
    ),
    NotificationKind(
        "loop",
        "needs_input",
        "Loop needs your input",
        "immediate",
        SEV_WARNING,
        attention=True,
    ),
    NotificationKind("loop", "progress", "Loop progress", "immediate", SEV_INFO),
    NotificationKind("inbox", "alert", "Inbox alert", "immediate", SEV_WARNING),
    NotificationKind("agent", "message", "Agent message", "immediate", SEV_INFO),
    NotificationKind("agent", "subagent", "Subagent update", "immediate", SEV_INFO),
    NotificationKind("hook", "fired", "Trigger fired", "immediate", SEV_INFO),
    NotificationKind("system", "warning", "System warning", "immediate", SEV_WARNING),
    NotificationKind("system", "error", "System error", "immediate", SEV_ERROR),
    NotificationKind("system", "info", "Notice", "immediate", SEV_INFO),
    NotificationKind("system", "success", "Success", "immediate", SEV_INFO),
    NotificationKind("system", "route_drift", "App route drift", "immediate", SEV_INFO),
    NotificationKind("system", "session", "Session notice", "immediate", SEV_INFO),
    NotificationKind(
        "learning", "retire", "Retired a learned signal", "immediate", SEV_INFO
    ),
    NotificationKind(
        "skills",
        "proposal",
        "Skill proposal",
        "immediate",
        SEV_INFO,
        attention=True,
        verifiable=True,
    ),
    NotificationKind(
        "guardrails",
        "autonomy_revocation",
        "Earned autonomy revoked",
        "immediate",
        SEV_WARNING,
        attention=True,
    ),
    NotificationKind(
        "learning",
        "proposal",
        "Learning proposal",
        "immediate",
        SEV_INFO,
        attention=True,
        verifiable=True,
    ),
    NotificationKind(
        "planning",
        "proposal",
        "Planning proposal",
        "immediate",
        SEV_INFO,
        attention=True,
        verifiable=True,
    ),
    NotificationKind(
        "system",
        "agent_request",
        "Agent request",
        "immediate",
        SEV_WARNING,
        attention=True,
        verifiable=True,
    ),
    NotificationKind(
        "system", "digest", "Daily digest", "immediate", SEV_INFO, attention=True
    ),
    NotificationKind(
        "system", "usage_recap", "Monthly usage recap", "digest", SEV_INFO
    ),
    NotificationKind(
        "apps", "update", "App update available", "immediate", SEV_INFO, attention=True
    ),
    NotificationKind(
        "knowledge",
        "research_finding",
        "Research report finding",
        "immediate",
        SEV_INFO,
        attention=True,
    ),
    NotificationKind(
        "learning", "report", "Identity report", "immediate", SEV_INFO, attention=True
    ),
    # timeout (`ConsoleState._approval_futures`), not a durable inbox row. Claiming
    NotificationKind(
        "approval", "requested", "Approval needed", "immediate", SEV_WARNING
    ),
    NotificationKind(
        "user",
        "note",
        "Note you captured",
        "badge",
        SEV_INFO,
        attention=True,
        verifiable=False,
    ),
    NotificationKind(
        GENERIC_SOURCE, GENERIC_KIND, "Uncategorized", "immediate", SEV_INFO
    ),
)

_LEGACY_FLAT: dict[str, tuple[str, str]] = {
    "cron": ("cron", "result"),
    "schedule": ("cron", "result"),
    "heartbeat": ("heartbeat", "status"),
    "loop": ("loop", "progress"),
    "inbox_alert": ("inbox", "alert"),
    "agent": ("agent", "message"),
    "subagent": ("agent", "subagent"),
    "hook": ("hook", "fired"),
    "warning": ("system", "warning"),
    "error": ("system", "error"),
    "info": ("system", "info"),
    "success": ("system", "success"),
    "app.route.drift": ("system", "route_drift"),
    "session": ("system", "session"),
    "feedback_retire": ("learning", "retire"),
    GENERIC_KIND: (GENERIC_SOURCE, GENERIC_KIND),
}

_ATTENTION_FLAT: dict[str, tuple[str, str]] = {
    "needs_input": ("loop", "needs_input"),
    "proposal": ("skills", "proposal"),
    "learning_proposal": ("learning", "proposal"),
    "planning_proposal": ("planning", "proposal"),
    "agent_request": ("system", "agent_request"),
    "digest": ("system", "digest"),
    "app_update": ("apps", "update"),
    "research_finding": ("knowledge", "research_finding"),
    "usage_recap": ("system", "usage_recap"),
    "report": ("learning", "report"),
    "approval": ("approval", "requested"),
    "user_note": ("user", "note"),
}

_WIRE_TO_PAIR: dict[str, tuple[str, str]] = {**_ATTENTION_FLAT, **_LEGACY_FLAT}

for _k in _KINDS:
    register(_k)


CRON = "cron"
HEARTBEAT = "heartbeat"
INBOX_ALERT = "inbox_alert"
AGENT = "agent"
SUBAGENT = "subagent"
HOOK = "hook"
WARNING = "warning"
ERROR = "error"
INFO = "info"
SUCCESS = "success"
APP_ROUTE_DRIFT = "app.route.drift"
SESSION = "session"
FEEDBACK_RETIRE = "feedback_retire"
USAGE_RECAP = "usage_recap"
RESEARCH_FINDING = "research_finding"
APPROVAL = "approval"
GENERIC = GENERIC_KIND

WIRE_CONSTANTS: tuple[str, ...] = (
    CRON,
    HEARTBEAT,
    INBOX_ALERT,
    AGENT,
    SUBAGENT,
    HOOK,
    WARNING,
    ERROR,
    INFO,
    SUCCESS,
    APP_ROUTE_DRIFT,
    SESSION,
    FEEDBACK_RETIRE,
    USAGE_RECAP,
    RESEARCH_FINDING,
    APPROVAL,
    GENERIC,
)

_unmapped = [c for c in WIRE_CONSTANTS if c not in _WIRE_TO_PAIR]
if _unmapped:  # pragma: no cover - import-time guard
    raise RuntimeError(
        f"notification wire constants missing a registration: {_unmapped}"
    )

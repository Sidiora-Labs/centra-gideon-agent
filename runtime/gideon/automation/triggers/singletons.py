"""Converge the built-in singleton system jobs onto one authoritative row per purpose."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from gideon.automation.triggers.models import Trigger, TriggerState

logger = logging.getLogger(__name__)

SYSTEM_OWNER = "system"

DIGEST = "notification-digest"
USAGE_RECAP = "usage-recap"
SOURCE_DIGEST = "source-digest"
IDENTITY_REPORT = "identity-report"
SELF_REMEDIATION = "self-remediation"
SELFQA_COMMIT_WATCH = "selfqa-commit-watch"
TRIAGE_DIGEST = "triage-digest"

_HISTORY_MARKS: tuple[str, ...] = (
    "last_run_id",
    "last_fired_at",
    "last_success_at",
    "last_failure_at",
)


@dataclass(frozen=True)
class SystemSingleton:
    """One built-in job that must exist at most once, named by PURPOSE rather than by id.

    ``trigger_id`` is the id the seeding site writes and the id the survivor ends up
    carrying; it is a convergence TARGET, not the identity. A home upgraded from a build
    that used another id (or that seeded twice through two paths) holds rows this registry
    still recognizes, which is why ``matches`` falls back to the
    (action provider, action, system-owned) tuple when a row predates ``Trigger.purpose``.
    """

    purpose: str
    trigger_id: str
    provider: str
    action: str = ""
    site: tuple[str, str] = ("", "")


SINGLETONS: tuple[SystemSingleton, ...] = (
    SystemSingleton(
        DIGEST,
        "system:notification-digest",
        "notification-digest",
        site=(
            "gideon.integrations.action_providers.digest_provider",
            "reconcile_digest_cron",
        ),
    ),
    SystemSingleton(
        USAGE_RECAP,
        "system:usage-recap",
        "usage-recap",
        site=(
            "gideon.integrations.action_providers.usage_recap_provider",
            "reconcile_usage_recap_cron",
        ),
    ),
    SystemSingleton(
        SOURCE_DIGEST,
        "system:source-digest",
        "source-digest",
        site=(
            "gideon.integrations.action_providers.source_digest_provider",
            "reconcile_source_digest_cron",
        ),
    ),
    SystemSingleton(
        IDENTITY_REPORT,
        "system:learning-identity-report",
        "identity-report",
        site=(
            "gideon.integrations.action_providers.identity_report_provider",
            "reconcile_identity_report_trigger",
        ),
    ),
    SystemSingleton(
        SELF_REMEDIATION,
        "system:self-remediation",
        "self-remediation",
        site=(
            "gideon.integrations.action_providers.remediation_provider",
            "reconcile_remediation_trigger",
        ),
    ),
    SystemSingleton(
        SELFQA_COMMIT_WATCH,
        "system:selfqa-commit-watch",
        "selfqa-commit-watch",
        site=("gideon.assurance.selfqa.install", "reconcile"),
    ),
    SystemSingleton(
        TRIAGE_DIGEST,
        "system:triage:digest",
        "run-workflow",
        "morning-triage",
        site=(
            "gideon.interfaces.dashboard.handlers.proactive",
            "api_proactive_install",
        ),
    ),
)

BY_PURPOSE: dict[str, SystemSingleton] = {entry.purpose: entry for entry in SINGLETONS}


def is_system_owned(trigger: Any) -> bool:
    return str(getattr(trigger, "created_by", "") or "").strip().lower() == SYSTEM_OWNER


def action_of(trigger: Any) -> tuple[str, str]:
    """The (provider, action) pair a trigger's workflow names, ``("", "")`` when it names none."""
    workflow = getattr(trigger, "workflow", None)
    action = workflow if isinstance(workflow, dict) else {}
    inline = action.get("inline")
    if isinstance(inline, dict) and inline:
        action = inline
    config = action.get("config")
    config = config if isinstance(config, dict) else {}
    return (
        str(action.get("provider") or "").strip(),
        str(config.get("workflow") or config.get("action") or "").strip(),
    )


def matches(entry: SystemSingleton, trigger: Any) -> bool:
    """Is *trigger* a live system-owned copy of *entry*'s singleton?

    A user-created row is never a copy, whatever it runs — convergence retires jobs the
    product seeded, never one someone authored. An already-retired copy is not a copy
    either: it was converged away once, and counting it again would let its switched-off
    flag hold the survivor down forever.
    """
    if not is_system_owned(trigger):
        return False
    if str(getattr(trigger, "state", "") or "") == TriggerState.RETIRED.value:
        return False
    declared = str(getattr(trigger, "purpose", "") or "").strip()
    if declared:
        return declared == entry.purpose
    if str(getattr(trigger, "id", "") or "") == entry.trigger_id:
        return True
    provider, action = action_of(trigger)
    if not provider or provider != entry.provider:
        return False
    return not entry.action or action == entry.action


def carries_history(trigger: Any) -> bool:
    if int(getattr(trigger, "run_count", 0) or 0) > 0:
        return True
    return any(str(getattr(trigger, name, "") or "") for name in _HISTORY_MARKS)


@dataclass
class Convergence:
    purpose: str
    survivor: str = ""
    enabled: bool = True
    retired: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def converged(self) -> bool:
        return bool(self.retired or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return dict(
            purpose=self.purpose,
            survivor=self.survivor,
            enabled=self.enabled,
            retired=list(self.retired),
            removed=list(self.removed),
        )


def _rank(entry: SystemSingleton, trigger: Trigger) -> tuple[int, int, str]:
    """Sort key picking the authoritative copy: the target id first, then the most history."""
    return (
        0 if trigger.id == entry.trigger_id else 1,
        -int(getattr(trigger, "run_count", 0) or 0),
        trigger.id,
    )


def copies(store: Any, entry: SystemSingleton) -> list[Trigger]:
    try:
        rows = store.load()
    except Exception:
        logger.debug(
            "singleton convergence: could not read the trigger store", exc_info=True
        )
        return []
    found = [row.trigger for row in rows if matches(entry, row.trigger)]
    return sorted(found, key=lambda trigger: _rank(entry, trigger))


def _retire(store: Any, trigger: Trigger, report: Convergence) -> None:
    """Retire a duplicate that carries history; delete one that never ran.

    A row with fires behind it is the only record of what those fires did, so it is switched
    off and marked retired rather than dropped — `autopause.resume_state` refuses to resume a
    retired trigger, so a retired copy cannot come back and fire beside the survivor.
    """
    if carries_history(trigger):
        trigger.enabled = False
        trigger.state = TriggerState.RETIRED.value
        trigger.next_fire_at = ""
        store.upsert(trigger)
        report.retired.append(trigger.id)
        return
    store.delete(trigger.id)
    report.removed.append(trigger.id)


def _promote(entry: SystemSingleton, survivor: Trigger) -> Trigger:
    """The survivor under the target id, so the next boot converges to the same row."""
    if survivor.id == entry.trigger_id:
        return survivor
    return replace(survivor, id=entry.trigger_id)


def converge(store: Any, purpose: str) -> Convergence:
    """Collapse every system copy of *purpose* into one row, preserving a disabled choice.

    Called by each built-in singleton's seeding site BEFORE it reads the store, so the site
    then edits the survivor in place instead of minting a second copy beside it. Enabling
    stays the operator's: a copy someone switched off is an explicit choice, so the survivor
    of a set holding one stays off — a convergence that silently re-armed an unattended job
    would be the worst failure this has.
    """
    entry = BY_PURPOSE.get(purpose)
    if entry is None:
        raise KeyError(f"{purpose!r} is not a registered built-in singleton")
    rows = copies(store, entry)
    report = Convergence(purpose)
    if not rows:
        return report
    survivor, duplicates = rows[0], rows[1:]
    report.enabled = all(bool(row.enabled) for row in rows)
    before = survivor.to_dict()
    promoted = _promote(entry, survivor)
    promoted.purpose = entry.purpose
    promoted.enabled = report.enabled
    report.survivor = promoted.id
    try:
        if promoted is not survivor or promoted.to_dict() != before:
            store.upsert(promoted)
        for duplicate in duplicates:
            _retire(store, duplicate, report)
        if promoted.id != survivor.id:
            _retire(store, survivor, report)
    except Exception:
        logger.warning(
            "singleton convergence could not write the %s survivor",
            purpose,
            exc_info=True,
        )
        return report
    if report.converged:
        logger.info(
            "converged the %s singleton onto %s (retired %s, removed %s)",
            purpose,
            report.survivor,
            ", ".join(report.retired) or "none",
            ", ".join(report.removed) or "none",
        )
    return report


def converge_all(
    store: Any, purposes: Iterable[str] | None = None
) -> list[Convergence]:
    """Converge every registered singleton, one best-effort pass per purpose."""
    names = list(purposes) if purposes is not None else [e.purpose for e in SINGLETONS]
    reports = []
    for purpose in names:
        try:
            reports.append(converge(store, purpose))
        except Exception:
            logger.warning(
                "singleton convergence failed for %s", purpose, exc_info=True
            )
    return reports

"""Delayed working-habit observations, bounded evidence, and accepted principles."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from gideon.cognition.learning.self_model import (
    CAPS,
    FACETS,
    KEY_PREFIX,
    MIN_SEEN_COUNT,
    Entry,
    Facet,
    Observation,
    Reaction,
    Reinforcement,
    build_proposal,
    plan_promotion,
    reinforce,
    trim_ring,
)

logger = logging.getLogger(__name__)

_ROW_CONFIDENCE = 1.0
_ROW_SOURCE = "self_model"

_CANDIDATE = "candidate"
_PENDING = "pending"

_KEEP_OBSERVATIONS = 8


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _slug(text: str) -> str:
    """A key-safe short hash of a pattern/session string.

    `_validate_key` demands `^[a-z][a-z0-9_.]*[a-z0-9]$` with no consecutive dots, and a pattern
    like "Gideon + edit_file,read_file" satisfies none of that. A hex digest is the stable,
    collision-resistant, always-valid key — the shape `memory_service` uses for its derived keys.
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def _candidate_key(pattern: str) -> str:
    return f"{KEY_PREFIX}.{_CANDIDATE}.{_slug(pattern)}"


def _pending_key(session_key: str) -> str:
    return f"{KEY_PREFIX}.{_PENDING}.{_slug(session_key or 'default')}"


def _entry_value(entry: Entry) -> dict:
    """The stored form of an `Entry` — `to_dict` minus the derived `memory_key` (not a field, so it
    must not round-trip back into the constructor)."""
    value = entry.to_dict()
    value.pop("memory_key", None)
    return value


def _reaction_to_previous(correction: bool) -> str:
    """This turn's message, read as the user's reaction to the PREVIOUS turn's work.

    A correction is the strongest negative the module has; anything else is the user MOVING ON —
    which is exactly `Reaction.ACCEPTED`'s definition ("moved on to the next step"), and the only
    positive signal that is something the user DID rather than something the harness assumed.
    """
    return Reaction.CORRECTED.value if correction else Reaction.ACCEPTED.value


def is_self_model_proposal(proposal_dict: dict) -> bool:
    """Whether an accepted `lesson_batch` proposal is a self-model principle this installer owns.

    Keyed on `source_cadence` (a durable record field) rather than a tag a reviewer might strip —
    the accept handler routes only these to `install_accepted_principle`, so an ordinary
    correction-derived lesson_batch is left to its own (no-op) install path.
    """
    return str(proposal_dict.get("source_cadence") or "") == "self_model"


def observed_pattern(route: str, tools: tuple[str, ...]) -> str:
    names = _SelfModelRows.tool_names(tools)
    return f"{route or 'direct'} + {','.join(names) or 'no-tools'}"


def _retrospection_entry(observation: Observation) -> Entry:
    return _SelfModelRows.retrospection(observation)


def _entry_from_row(key: str, value: Any) -> Entry | None:
    return _SelfModelRows.entry(key, value)


def _load_value(service, key: str) -> Any:
    row = service.get_semantic(key)
    return _SelfModelRows.decode(row) if row else None


def _write_row(service, key: str, value: dict) -> None:
    _SelfModelRows(service).write(key, value)


def load_live_entries(service) -> list[Entry]:
    return _SelfModelRows(service).live()


def _load_candidate(service, pattern: str) -> Reinforcement | None:
    return _SelfModelRows(service).candidate(pattern)


def _save_candidate(service, record: Reinforcement) -> None:
    _SelfModelRows(service).remember_candidate(record)


def _append_retrospection(service, observation: Observation, live: list[Entry]) -> None:
    _SelfModelRows(service).remember_retrospection(observation, live)


def observe_turn(
    service,
    *,
    session_key: str,
    route: str,
    tools: tuple[str, ...],
    succeeded: bool,
    correction: bool,
    staging_store=None,
    min_evidence: int = MIN_SEEN_COUNT,
    now: str = "",
) -> dict:
    cycle = _ObservedTurnCycle(service, staging_store, min_evidence)
    return cycle.advance(session_key, route, tools, succeeded, correction, now)


def _resolve_pending(
    service, *, pending: dict, correction: bool, staging_store, min_evidence: int
) -> dict:
    return _ObservedTurnCycle(service, staging_store, min_evidence).resolve(
        pending, correction
    )


def _stage_observation(observation: Observation, staging_store) -> bool:
    from gideon.cognition.learning.gate import Cadence
    from gideon.cognition.learning.staging import get_store

    destination = staging_store
    if destination is None:
        destination = get_store()
    fields = {
        "cadence": Cadence.PER_TURN.value,
        "kind": "self_model",
        "content": (
            f"self-model observation: {observation.pattern} → "
            f"{observation.reaction} after {'success' if observation.succeeded else 'failure'}"
        ),
        "meta": observation.to_dict(),
    }
    receipt = destination.stage(**fields)
    return bool(receipt)


def _file_proposal(proposal, record: Reinforcement, *, min_evidence: int) -> bool:
    from gideon.cognition.learning import proposals

    submission = dict(
        kind=proposals.Kind.LESSON_BATCH.value,
        title=f"Observed working habit: {proposal.pattern}"[:120],
        body=proposal.body,
        target=f"{KEY_PREFIX}.{proposal.facet}",
        provenance="inferred",
        source_cadence="self_model",
        evidence_refs=list(proposal.evidence),
        evidence_strength="correlated",
        confidence=proposal.confidence,
        tags=["self_model", proposal.facet],
        occurrences=record.seen_count,
        min_evidence=min_evidence,
    )
    _, queued = proposals.enqueue(**submission)
    return queued is not None


def _principle_slug(pattern: str) -> str:
    tokens = (match.group(0) for match in re.finditer(r"[a-z0-9]+", pattern.lower()))
    readable = "_".join(tokens)[:48].strip("_")
    return readable if readable else _slug(pattern)


def install_accepted_principle(service, proposal_dict: dict) -> bool:
    return _PrincipleInstaller(service).install(proposal_dict)


def _displace_for_cap(service, *, incoming: Entry) -> None:
    _PrincipleInstaller(service).make_room(incoming)


class _SelfModelRows:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def tool_names(tools):
        names = set()
        for tool in tools:
            if tool:
                names.add(tool)
        return sorted(names)

    @staticmethod
    def decode(row):
        try:
            return json.loads(row["value_json"])
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def entry(key, value):
        segments = key.split(".", 3)
        if len(segments) != 4:
            return None
        facet, fallback = segments[2:]
        if facet not in FACETS or not isinstance(value, dict):
            return None
        fields = {"facet": facet}
        conversions = (
            ("key", str, fallback),
            ("body", str, ""),
            ("seen_count", int, 0),
            ("confidence", float, 0.0),
            ("evidence", list, []),
            ("created_at", str, ""),
            ("last_seen_at", str, ""),
        )
        for name, convert, default in conversions:
            fields[name] = convert(value.get(name) or default)
        return Entry(**fields)

    @staticmethod
    def retrospection(observation):
        identity = _slug(f"{observation.pattern}|{observation.at}")
        state = "success" if observation.succeeded else "failure"
        entry = Entry(
            facet=Facet.RETROSPECTION.value,
            key=identity,
            body=f"{observation.reaction} after {state} via {observation.route or 'direct'}",
            seen_count=1,
            confidence=1.0 if observation.succeeded else 0.0,
        )
        entry.created_at = observation.at
        entry.last_seen_at = observation.at
        return entry

    def write(self, key, value):
        failure = self.service.set_semantic(key, value, _ROW_CONFIDENCE, _ROW_SOURCE)
        if failure is None:
            return
        code, reason = failure
        logger.debug("self-model row %s rejected (%s): %s", key, code, reason)

    def live(self):
        entries = []
        for row in self.service.get_all_semantic():
            key = str(row.get("key") or "")
            if key.startswith(f"{KEY_PREFIX}."):
                decoded = self.decode(row)
                entry = _entry_from_row(key, decoded)
                if entry is not None:
                    entries.append(entry)
        return entries

    def candidate(self, pattern):
        stored = _load_value(self.service, _candidate_key(pattern))
        if not isinstance(stored, dict):
            return None
        history = []
        for item in stored.get("observations") or []:
            if not isinstance(item, dict):
                continue
            arguments: dict = {
                "pattern": str(item.get("pattern") or pattern),
                "route": str(item.get("route") or ""),
                "tools": tuple(item.get("tools") or ()),
                "succeeded": bool(item.get("succeeded", True)),
                "reaction": str(item.get("reaction") or Reaction.NEUTRAL.value),
                "at": str(item.get("at") or ""),
            }
            history.append(Observation(**arguments))
        return Reinforcement(
            pattern,
            int(stored.get("seen_count") or 0),
            float(stored.get("score") or 0.0),
            history,
        )

    def remember_candidate(self, record):
        payload = dict(
            pattern=record.pattern,
            seen_count=record.seen_count,
            score=round(record.score, 4),
        )
        payload["observations"] = list(
            map(lambda item: item.to_dict(), record.observations[-_KEEP_OBSERVATIONS:])
        )
        _write_row(self.service, _candidate_key(record.pattern), payload)

    def remember_retrospection(self, observation, live):
        newest = _retrospection_entry(observation)
        _write_row(self.service, newest.memory_key, _entry_value(newest))
        population = list(
            filter(lambda item: item.facet == Facet.RETROSPECTION.value, live)
        )
        population.append(newest)
        retained = set(map(lambda item: item.memory_key, trim_ring(population)))
        removals = (item for item in population if item.memory_key not in retained)
        for item in removals:
            self.service.delete_semantic(item.memory_key, source=_ROW_SOURCE)


class _ObservedTurnCycle:
    def __init__(self, service, staging_store, min_evidence):
        self.service = service
        self.staging_store = staging_store
        self.min_evidence = min_evidence

    def advance(self, session_key, route, tools, succeeded, correction, now):
        report = dict(resolved=False, staged=False, proposed=False, pattern="")
        if self.service is None or not getattr(self.service, "has_vector", False):
            return report
        stamp = now or _now()
        try:
            parked = _load_value(self.service, _pending_key(session_key))
            if isinstance(parked, dict) and parked.get("pattern"):
                resolved = _resolve_pending(
                    self.service,
                    pending=parked,
                    correction=correction,
                    staging_store=self.staging_store,
                    min_evidence=self.min_evidence,
                )
                report.update(resolved)
            self.park(session_key, route, tools, succeeded, stamp)
        except Exception:
            logger.debug("self-model observer failed", exc_info=True)
        return report

    def park(self, session_key, route, tools, succeeded, stamp):
        pattern = observed_pattern(route, tools)
        _write_row(
            self.service,
            _pending_key(session_key),
            {
                "pattern": pattern,
                "route": route,
                "tools": _SelfModelRows.tool_names(tools),
                "succeeded": bool(succeeded),
                "at": stamp,
            },
        )

    def resolve(self, pending, correction):
        report = dict(
            resolved=True, staged=False, proposed=False, pattern=str(pending["pattern"])
        )
        observation = Observation(
            pattern=str(pending["pattern"]),
            route=str(pending.get("route") or ""),
            tools=tuple(pending.get("tools") or ()),
            succeeded=bool(pending.get("succeeded", True)),
            reaction=_reaction_to_previous(correction),
            at=str(pending.get("at") or _now()),
        )
        report["staged"] = _stage_observation(observation, self.staging_store)
        population = load_live_entries(self.service)
        history = _load_candidate(self.service, observation.pattern)
        record = reinforce(history, observation)
        for operation, arguments in (
            (_save_candidate, (self.service, record)),
            (_append_retrospection, (self.service, observation, population)),
        ):
            operation(*arguments)
        facet = Facet.PRINCIPLE.value
        plan = plan_promotion(facet=facet, reinforcement=record, current=population)
        proposal = build_proposal(facet=facet, reinforcement=record, plan=plan)
        if proposal is not None:
            report["proposed"] = _file_proposal(
                proposal, record, min_evidence=self.min_evidence
            )
        return report


class _PrincipleInstaller:
    def __init__(self, service):
        self.service = service

    def install(self, proposal):
        if self.service is None or not getattr(self.service, "has_vector", False):
            return False
        body = str(proposal.get("body") or "").strip()
        if not body:
            return False
        pattern = str(proposal.get("title") or body)
        fields: dict = dict(
            facet=Facet.PRINCIPLE.value,
            key=_principle_slug(pattern),
            body=body,
            seen_count=int(proposal.get("reinforcements") or 0),
            confidence=float(proposal.get("confidence") or 0.0),
            evidence=list(proposal.get("evidence_refs") or []),
            created_at=_now(),
            last_seen_at=_now(),
        )
        incoming = Entry(**fields)
        _displace_for_cap(self.service, incoming=incoming)
        _write_row(self.service, incoming.memory_key, _entry_value(incoming))
        return True

    def make_room(self, incoming):
        capacity = CAPS.get(Facet.PRINCIPLE.value, 0)
        competing = []
        for entry in load_live_entries(self.service):
            if entry.facet == Facet.PRINCIPLE.value and entry.key != incoming.key:
                competing.append(entry)
        if len(competing) >= capacity:
            displaced = min(
                competing,
                key=lambda item: (item.confidence, item.seen_count, item.created_at),
            )
            self.service.delete_semantic(displaced.memory_key, source=_ROW_SOURCE)

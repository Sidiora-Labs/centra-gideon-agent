"""Memory slots — bounded, always-injected registers (MEMORY-GRAPH-AND-VAULT §6/§6.1 — MGAV-8).

A slot is the one memory class that does NOT compete for recall. Facts, lessons and episodes
are *retrieved* when a query looks like them; a slot is a register the harness reads on every
session regardless of what the user asked. That makes slots the highest-leverage and the most
dangerous memory class at once, so the three properties below are enforced mechanically here
rather than left to whoever writes the next caller.

**1. Every slot is capped, and over-cap FAILS LOUDLY.** An always-injected register with no
ceiling is a slow context leak: each append costs every future turn, forever. The obvious
implementations are both wrong — silently truncating destroys what the user just said, and
silently dropping the write makes the system look like it agreed. So an over-cap append raises
:class:`SlotCapExceeded` carrying a :class:`TrimProposal` that names the exact lines whose
removal would make room. The caller must surface it; nothing here decides on the user's behalf
which memory to lose.

**2. Built-ins are LAZY.** The six built-in slots (persona / preferences / pending_items /
self_notes / glossary / self_model) are *descriptors*, not rows. Nothing is written until a
first real append, so a fresh install has zero `slot.*` rows and the injected block is empty
rather than six confusing empty headers. Materialising them eagerly would also mean every
install ships rows nobody wrote, which `audit_home` would then have to explain.

**3. Appends never resurrect a human tombstone.** A tombstoned line stays in the row (that is
what makes the guard possible) with ``tombstoned=True``, and :func:`append` refuses to re-add
text a HUMAN tombstoned. An agent-driven reflection hook that re-derives the same line the user
just deleted is not a bug the user can see — it looks like the system ignoring them — which is
why the check lives at the write primitive and not in each hook.

Writes go through the store's ``set_semantic``, so the memory event log (WAL) and
``undo_event`` cover slot writes with no separate journal. Reads and pure decisions live here;
this module builds no store and imports none, so ``vector_memory`` can depend on it one-way for
its put-time cap check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

SLOT_PREFIX = "slot."

DEFAULT_SLOT_CAP_CHARS = 600

SLOTS_BLOCK_MAX_CHARS = 1400

SLOTS_BLOCK_MIN_CHARS = 200
SLOTS_BLOCK_HARD_MAX_CHARS = 4000

_TRUNCATION_MARKER = "\n… [slots truncated]"


def resolve_block_limit(configured: object) -> int:
    """The block budget to render with, clamped into the structural range.

    Takes ``object`` because the caller reads it out of config, where a hand-edited value can
    be anything: an unreadable one falls back to the default rather than raising, since a bad
    number in config.json must not be able to stop a session from starting.
    """
    try:
        value = int(configured)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return SLOTS_BLOCK_MAX_CHARS
    if value < SLOTS_BLOCK_MIN_CHARS:
        return SLOTS_BLOCK_MIN_CHARS
    return SLOTS_BLOCK_HARD_MAX_CHARS if value > SLOTS_BLOCK_HARD_MAX_CHARS else value


@dataclass(frozen=True)
class SlotSpec:
    """A built-in slot's identity and budget. A descriptor only — see the module docstring on
    laziness: holding a spec never implies a row exists."""

    name: str
    title: str
    cap_chars: int
    scope: str = "global"
    description: str = ""


BUILTIN_SLOTS: dict[str, SlotSpec] = {
    "persona": SlotSpec(
        name="persona",
        title="Persona",
        cap_chars=400,
        description="How the user wants the assistant to present itself.",
    ),
    "preferences": SlotSpec(
        name="preferences",
        title="Preferences",
        cap_chars=500,
        description="Standing preferences the assistant should default to.",
    ),
    "pending_items": SlotSpec(
        name="pending_items",
        title="Pending items",
        cap_chars=700,
        description="Open threads the user expects to be picked back up.",
    ),
    "self_notes": SlotSpec(
        name="self_notes",
        title="Self notes",
        cap_chars=500,
        description="The assistant's own working notes about this user's setup.",
    ),
    "glossary": SlotSpec(
        name="glossary",
        title="Glossary",
        cap_chars=600,
        scope="workspace",
        description="Project vocabulary: terms that mean something specific here.",
    ),
    "self_model": SlotSpec(
        name="self_model",
        title="Self model",
        cap_chars=500,
        description="Behavioural principles reinforced enough to act on (see learning.self_model).",
    ),
}

BLOCK_ORDER = (
    "persona",
    "preferences",
    "self_model",
    "glossary",
    "pending_items",
    "self_notes",
)


def key_for(name: str) -> str:
    return "{}{}".format(SLOT_PREFIX, name)


def name_from_key(key: str) -> str:
    return key.removeprefix(SLOT_PREFIX) if key.startswith(SLOT_PREFIX) else ""


def spec_for(name: str) -> SlotSpec:
    if name in BUILTIN_SLOTS and BUILTIN_SLOTS[name] is not None:
        return BUILTIN_SLOTS[name]
    title = name.replace("_", " ").strip().capitalize()
    return SlotSpec(name, title or name, DEFAULT_SLOT_CAP_CHARS)


def cap_for(name: str) -> int:
    descriptor = spec_for(name)
    return descriptor.cap_chars


def cap_for_key(key: str) -> int:
    name = name_from_key(key)
    return cap_for(name)


def _now_iso() -> str:
    stamp = datetime.now(timezone.utc)
    return stamp.isoformat()


@dataclass(frozen=True)
class SlotLine:
    text: str
    added_at: str = ""
    tombstoned: bool = False
    tombstoned_by: str = ""
    reinforcements: int = 1

    def to_dict(self) -> dict[str, Any]:
        fields = ("text", "added_at", "tombstoned", "tombstoned_by", "reinforcements")
        return {name: getattr(self, name) for name in fields}

    @classmethod
    def from_dict(cls, raw: Any) -> "SlotLine":
        if not isinstance(raw, dict):
            return cls(text=raw if isinstance(raw, str) else str(raw))
        values: dict = {name: str(raw.get(name, "")) for name in ("text", "added_at")}
        values["tombstoned"] = bool(raw.get("tombstoned", False))
        values["tombstoned_by"] = str(raw.get("tombstoned_by", ""))
        values["reinforcements"] = int(raw.get("reinforcements", 1) or 1)
        return cls(**values)


def parse_lines(value: Any) -> list[SlotLine]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [] if not cleaned else [SlotLine(cleaned)]
    payload = value.get("lines", []) if isinstance(value, dict) else value
    if not isinstance(payload, list):
        return []
    result = []
    for item in payload:
        line = SlotLine.from_dict(item)
        if line.text.strip():
            result.append(line)
    return result


def live_lines(lines: list[SlotLine]) -> list[SlotLine]:
    return list(filter(lambda line: not line.tombstoned, lines))


def to_value(lines: list[SlotLine]) -> dict[str, Any]:
    records = [line.to_dict() for line in lines]
    return {"lines": records}


def live_chars(value: Any) -> int:
    lengths = [len(line.text) for line in parse_lines(value) if not line.tombstoned]
    return sum(lengths) + max(0, len(lengths) - 1)


@dataclass(frozen=True)
class TrimProposal:
    slot: str
    cap_chars: int
    current_chars: int
    incoming_chars: int
    drop_candidates: list[str] = field(default_factory=list)

    @property
    def over_by(self) -> int:
        excess = self.current_chars + self.incoming_chars - self.cap_chars
        return excess if excess > 0 else 0

    @property
    def message(self) -> str:
        prefix = (
            f"slot {self.slot!r} is at {self.current_chars}/{self.cap_chars} chars and the new "
            f"line adds {self.incoming_chars}, exceeding the cap by {self.over_by}. "
            f"Nothing was written. Remove {len(self.drop_candidates)} line(s) to make room: "
        )
        return prefix + "; ".join(map(repr, self.drop_candidates))

    def to_dict(self) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in (
                "slot",
                "cap_chars",
                "current_chars",
                "incoming_chars",
                "over_by",
            )
        }
        result.update(drop_candidates=list(self.drop_candidates), message=self.message)
        return result


class SlotCapExceeded(Exception):
    def __init__(self, proposal: TrimProposal):
        super().__init__(proposal.message)
        self.proposal = proposal


class _SlotBudget:
    def __init__(self, name, existing, incoming):
        self.name, self.existing = name, existing
        self.cap = cap_for(name)
        self.live = live_lines(existing)
        self.current = live_chars(to_value(existing))
        self.incoming = len(incoming) + int(bool(self.live))

    def proposal(self):
        remaining = self.current + self.incoming - self.cap
        candidates = []
        for line in self.live:
            if remaining <= 0:
                break
            candidates.append(line.text)
            remaining -= len(line.text) + 1
        return TrimProposal(
            self.name, self.cap, self.current, self.incoming, candidates
        )


def propose_trim(name: str, existing: list[SlotLine], incoming: str) -> TrimProposal:
    return _SlotBudget(name, existing, incoming).proposal()


class _SlotStore(Protocol):
    """The semantic-row operations required by a slot ledger."""

    def get_semantic(self, key: str) -> dict | None: ...

    def set_semantic(
        self, key: str, value: object, confidence: float, source: str
    ) -> tuple[Any, str] | None: ...


def _raw_value(store: _SlotStore, name: str) -> Any:
    row = store.get_semantic(key_for(name))
    if row:
        if "value" in row and row.get("value") is not None:
            return row["value"]
        try:
            return json.loads(row.get("value_json") or "null")
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def is_materialized(store: _SlotStore, name: str) -> bool:
    row = store.get_semantic(key_for(name))
    return row is not None


def load(store: _SlotStore, name: str) -> list[SlotLine]:
    value = _raw_value(store, name)
    return parse_lines(value)


class _SlotLedger:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self.lines = load(store, name)

    def write(self, lines, source):
        return self.store.set_semantic(key_for(self.name), to_value(lines), 1.0, source)

    def append(self, text, source, reinforce):
        match = next((line for line in self.lines if line.text == text), None)
        if match is not None:
            if match.tombstoned and match.tombstoned_by == "human":
                return self.lines
            if not match.tombstoned:
                if not reinforce:
                    return self.lines
                from dataclasses import replace

                updated = [
                    (
                        replace(match, reinforcements=match.reinforcements + 1)
                        if line.text == text and not line.tombstoned
                        else line
                    )
                    for line in self.lines
                ]
                self.write(updated, source)
                return updated
        proposal = propose_trim(self.name, self.lines, text)
        if proposal.over_by > 0:
            raise SlotCapExceeded(proposal)
        rows = [*self.lines, SlotLine(text, added_at=_now_iso())]
        rejection = self.write(rows, source)
        if rejection is None:
            return rows
        code, reason = rejection
        raise SlotCapExceeded(
            TrimProposal(
                self.name,
                cap_for(self.name),
                live_chars(to_value(self.lines)),
                len(text),
                [f"store rejected the write ({code}): {reason}"],
            )
        )

    def tombstone(self, text, actor, source):
        from dataclasses import replace

        positions = {
            index
            for index, line in enumerate(self.lines)
            if line.text == text and not line.tombstoned
        }
        if not positions:
            return False
        changed = [
            (
                replace(line, tombstoned=True, tombstoned_by=actor)
                if index in positions
                else line
            )
            for index, line in enumerate(self.lines)
        ]
        self.write(changed, source)
        return True


def append(
    store: _SlotStore,
    name: str,
    text: str,
    *,
    source: str = "user_explicit",
    reinforce: bool = False,
) -> list[SlotLine]:
    cleaned = text.strip()
    if not cleaned:
        return load(store, name)
    return _SlotLedger(store, name).append(cleaned, source, reinforce)


def tombstone(
    store: _SlotStore,
    name: str,
    text: str,
    *,
    actor: str = "human",
    source: str = "user_explicit",
) -> bool:
    cleaned = text.strip()
    return _SlotLedger(store, name).tombstone(cleaned, actor, source)


def over_cap(store: _SlotStore, names: list[str] | None = None) -> dict[str, int]:
    candidates = list(BUILTIN_SLOTS) if names is None else names
    excesses = {}
    for name in candidates:
        if is_materialized(store, name):
            occupied = live_chars(_raw_value(store, name))
            excess = occupied - cap_for(name)
            if excess > 0:
                excesses[name] = excess
    return excesses


class _SlotProjection:
    def __init__(self, store, order):
        self.store, self.order = store, order

    def sections(self):
        for name in self.order:
            if not is_materialized(self.store, name):
                continue
            records = live_lines(parse_lines(_raw_value(self.store, name)))
            if records:
                title = spec_for(name).title
                yield title + ":\n" + "\n".join("- " + row.text for row in records)

    def render(self, limit):
        sections = list(self.sections())
        if not sections:
            return ""
        body = "[MEMORY SLOTS]\n" + "\n".join(sections) + "\n"
        if len(body) > limit:
            remaining = max(0, limit - len(_TRUNCATION_MARKER))
            return body[:remaining] + _TRUNCATION_MARKER
        return body


def render_slots_block(
    store: _SlotStore,
    *,
    names: list[str] | None = None,
    limit: int = SLOTS_BLOCK_MAX_CHARS,
) -> str:
    order = list(BLOCK_ORDER) if names is None else names
    return _SlotProjection(store, order).render(limit)

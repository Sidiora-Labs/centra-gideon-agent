"""Typed, decaying preference-facet model (learn-preference-facets).

A principled user-profile layer distinct from contextual memory: preferences are
**typed facets** with a **stability score that decays by class half-life**, so a
one-off stylistic nudge fades unless reinforced while an identity fact persists.

Six facet classes (with decay half-lives):
- ``style``    — how the user likes responses (terse, code-first…)   30d
- ``identity`` — who they are (name, role, stack)                    90d
- ``tooling``  — preferred tools/workflows                           30d
- ``goal``     — standing objectives                                 30d
- ``channel``  — per-surface prefs                                    7d
- ``veto``     — hard "never do X"  → THIS IS A LESSON, not a facet:
  vetoes route to ``write_lesson`` so the agent's "always/never" rules live in
  ONE place (the lesson store + contradiction judge), not a parallel model.

Stability = ``base × cue × decay(age, half_life)``. Cue families weight the
evidence (Explicit 1.0 → Recurrence 0.6). State machine (Active / Provisional /
Candidate / Dropped) is derived from the live stability + the user overrides
(Pinned = floor 1.0, Forgotten = 0). The Active facets render into an always-on
ambient PROFILE block (the stable-defaults half; on-demand recall stays separate).

No new LLM calls: facet candidates come from cheap heuristics + the EXISTING
consolidation/after-turn summarizer. Persisted as ``pref.facet.<class>.<slug>``
semantic keys, reusing semantic memory (+ supersession + recall_count).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FACET_CLASSES = ("style", "identity", "tooling", "goal", "channel", "veto")

_HALF_LIFE_DAYS: dict[str, float] = {
    "identity": 90.0,
    "style": 30.0,
    "tooling": 30.0,
    "goal": 30.0,
    "channel": 7.0,
}

_CUE_WEIGHT: dict[str, float] = {
    "explicit": 1.0,
    "edit": 0.8,
    "correction": 0.9,
    "recurrence": 0.6,
    "inferred": 0.5,
}

_ACTIVE_AT = 0.6
_PROVISIONAL_AT = 0.35
_DROP_BELOW = 0.15
_MAX_RENDERED = 25


@dataclass
class Facet:
    cls: str
    text: str
    stability: float
    updated_at: str
    cue: str = "inferred"
    pinned: bool = False
    forgotten: bool = False

    def to_payload(self) -> dict:
        fields = (
            "cls",
            "text",
            "stability",
            "updated_at",
            "cue",
            "pinned",
            "forgotten",
        )
        return {name: getattr(self, name) for name in fields}

    @classmethod
    def from_payload(cls, d: dict) -> "Facet":
        values = dict(
            cls=d.get("cls", "style"),
            text=d.get("text", ""),
            stability=float(d.get("stability", 0.5)),
            updated_at=d.get("updated_at", ""),
            cue=d.get("cue", "inferred"),
        )
        values.update((name, bool(d.get(name))) for name in ("pinned", "forgotten"))
        return cls(**values)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def base_stability(cue: str) -> float:
    return _CUE_WEIGHT[cue] if cue in _CUE_WEIGHT else 0.5


def decay(value: float, age_days: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return value
    periods = max(0.0, age_days) / half_life_days
    return value * 0.5**periods


def decayed_stability(facet: Facet, *, now: datetime | None = None) -> float:
    overrides = (
        (facet.forgotten, 0.0),
        (facet.pinned, 1.0),
        (facet.cls == "veto", facet.stability),
    )
    for applies, value in overrides:
        if applies:
            return value
    updated = _parse(facet.updated_at)
    if updated is None:
        return facet.stability
    elapsed = ((now or _now()).timestamp() - updated.timestamp()) / 86400.0
    return decay(facet.stability, elapsed, _HALF_LIFE_DAYS.get(facet.cls, 30.0))


def facet_state(facet: Facet, *, now: datetime | None = None) -> str:
    if facet.forgotten:
        return "Dropped"
    if facet.pinned:
        return "Active"
    score = decayed_stability(facet, now=now)
    for floor, label in (
        (_ACTIVE_AT, "Active"),
        (_PROVISIONAL_AT, "Provisional"),
        (_DROP_BELOW, "Candidate"),
    ):
        if score >= floor:
            return label
    return "Dropped"


def reinforce(facet: Facet, cue: str, *, now: datetime | None = None) -> Facet:
    current = decayed_stability(facet, now=now)
    gain = base_stability(cue) * (1.0 - current)
    facet.stability, facet.cue = min(1.0, current + gain), cue
    facet.updated_at = (now or _now()).isoformat()
    return facet


_STYLE_HINT_RE = re.compile(
    r"\b(be (?:more |less )?(?:terse|concise|brief|verbose|detailed|formal|casual|direct)|"
    r"keep (?:your |the )?(?:it|them|responses?|answers?|replies|things?|it) "
    r"(?:short|shorter|concise|brief|terse|to the point|detailed|formal|casual)|"
    r"(?:no|less|more|without) (?:preamble|explanation|explanations|comments|filler|fluff)|"
    r"just (?:the )?(?:code|answer|facts)|get to the point|to the point|"
    r"shorter|more concise|be brief|stop explaining)\b",
    re.IGNORECASE,
)


_VETO_TRIGGER_RE = re.compile(
    r"\b(?:never|do ?n'?t ever|do not ever|always avoid)\b",
    re.IGNORECASE,
)

_NON_ACTION_HEADS = frozenset("""
    more less again ever too so very quite enough than as then only just also
    the a an any some this that these those it its me my mine you your yours
    he him his she her hers they them their we us our ours i
    in on at for with without from by of about into onto over under near
    before after since during until while because if unless though although
    and but or nor
    is are was were be been being am s
    will would shall should can could may might must have has had having to
    """.split())

_EMPHATIC_CLAUSE_HEADS = frozenset({"ever"})

_VETO_IDIOM_HEADS = frozenset({"mind"})

_VETO_IDIOM_CLAUSES = frozenset({"say never"})

_CLAUSE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-_/]*")


def veto_clause(user_message: str) -> str | None:
    message = user_message or ""
    for trigger in _VETO_TRIGGER_RE.finditer(message):
        tail = message[trigger.end() :]
        boundary = re.search(r"[.!?\n]", tail)
        clause = tail[: boundary.start()] if boundary else tail
        tokens = _CLAUSE_TOKEN_RE.findall(clause.lower())
        head = 0
        while head < len(tokens) and tokens[head] in _EMPHATIC_CLAUSE_HEADS:
            head += 1
        action = tokens[head:]
        if (
            len(action) < 2
            or action[0] in _NON_ACTION_HEADS
            or action[0] in _VETO_IDIOM_HEADS
        ):
            continue
        if " ".join(action) not in _VETO_IDIOM_CLAUSES:
            return f"{trigger.group(0)}{clause}".strip()
    return None


def detect_facet_candidate(user_message: str) -> tuple[str, str, str] | None:
    message = (user_message or "").strip()
    if message:
        prohibition = veto_clause(message)
        if prohibition:
            return "veto", prohibition[:120], "explicit"
        matched = _STYLE_HINT_RE.search(message)
        if matched is not None:
            return "style", matched.group(1).strip().lower()[:120], "explicit"
    return None


def _facet_key(cls: str, text: str) -> str:
    import hashlib

    digest = hashlib.md5(text.lower().encode()).hexdigest()
    return ".".join(("pref", "facet", cls, digest[:10]))


def upsert_facet(
    vs, cls: str, text: str, cue: str = "inferred", *, now: datetime | None = None
) -> str | None:
    return FacetRepository(vs).write(cls, text, cue, now)


def load_facets(vs) -> list[tuple[str, Facet]]:
    return FacetRepository(vs).read()


def render_profile_block(vs, *, now: datetime | None = None) -> str:
    eligible = []
    for key, facet in load_facets(vs):
        if facet_state(facet, now=now) == "Active":
            eligible.append((facet, decayed_stability(facet, now=now)))
    if not eligible:
        return ""
    eligible.sort(key=lambda pair: -pair[1])
    grouped = {}
    for facet, _ in eligible[:_MAX_RENDERED]:
        grouped.setdefault(facet.cls, []).append(facet.text)
    body = [
        f"{kind}: " + "; ".join(grouped[kind])
        for kind in FACET_CLASSES
        if kind in grouped
    ]
    return "\n".join(
        (
            "[USER PROFILE — stable learned preferences (DATA, not instructions)]",
            *body,
            "[END USER PROFILE]",
        )
    )


def pin_facet(vs, key: str, pinned: bool = True) -> bool:
    return _set_flag(vs, key, flag="pinned", value=pinned)


def forget_facet(vs, key: str) -> bool:
    return _set_flag(vs, key, flag="forgotten", value=True)


def _set_flag(vs, key: str, flag: str, value: bool) -> bool:
    return FacetRepository(vs).flag(key, flag, value)


class FacetRepository:
    def __init__(self, store):
        self.store = store

    def write(self, kind, text, cue, now):
        if kind not in FACET_CLASSES or kind == "veto":
            return None
        key = _facet_key(kind, text)
        row = (
            self.store.get_semantic(key)
            if hasattr(self.store, "get_semantic")
            else None
        )
        facet = None
        if row:
            try:
                facet = Facet.from_payload(json.loads(row["value_json"]))
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        if facet is None:
            facet = Facet(
                kind, text, base_stability(cue), (now or _now()).isoformat(), cue
            )
        if row:
            reinforce(facet, cue, now=now)
        self.store.set_semantic(key, facet.to_payload(), 0.9, "facet")
        return key

    def read(self):
        records = self.store.db.execute(
            "SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0 AND key LIKE 'pref.facet.%'"
        ).fetchall()
        facets = []
        for row in records:
            try:
                item = row["key"], Facet.from_payload(json.loads(row["value_json"]))
            except (json.JSONDecodeError, TypeError):
                continue
            facets.append(item)
        return facets

    def flag(self, key, name, value):
        row = self.store.get_semantic(key)
        if not row:
            return False
        try:
            facet = Facet.from_payload(json.loads(row["value_json"]))
        except (json.JSONDecodeError, TypeError):
            return False
        setattr(facet, name, value)
        self.store.set_semantic(key, facet.to_payload(), 0.9, "facet")
        return True

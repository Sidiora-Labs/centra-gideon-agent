"""Holder attribution — the optional "whose claim is this?" axis on semantic rows.

MEMORY-GRAPH-AND-VAULT §4.2 (MGAV-5). A plain semantic row asserts a fact about the
world. A *claim* asserts that somebody said something, and the two must not be stored
the same way: "the migration ships Friday" and "Alex says the migration ships Friday"
have different truth conditions, and collapsing them is how a memory system starts
confidently repeating one person's guess as established fact.

Deliberately an AXIS on existing rows, not a claims subsystem:

* ``holder`` — ``""`` (plain fact, the default and today's behavior), ``user``,
  ``assistant``, ``person:<entity_id>``, or ``external``.
* ``weight`` — a coarse 0.05-quantized strength, capped by holder class. There is no
  ``kind`` column: kind inference stays key-prefix based (``claim.*``), which is the
  recon invariant the rest of the memory system already relies on.

Two rules carry the safety here:

1. **A plain fact is never re-weighted.** ``holder=""`` keeps its full weight, so
   introducing this axis cannot silently down-rank every record written before it.
2. **The caps are a ceiling, not a rejection.** A self-report claiming certainty 0.95
   is clamped to its class ceiling and kept — dropping the write instead would lose a
   memory the user gave us because a model over-claimed about it.
"""

from __future__ import annotations

from typing import Any, Iterable

CLAIM_PREFIX = "claim."

HOLDER_NONE = ""
HOLDER_USER = "user"
HOLDER_ASSISTANT = "assistant"
HOLDER_EXTERNAL = "external"
HOLDER_PERSON_PREFIX = "person:"

HOLDERS = (HOLDER_NONE, HOLDER_USER, HOLDER_ASSISTANT, HOLDER_EXTERNAL)

WEIGHT_QUANTUM = 0.05

SELF_REPORT_CAP = 0.75
SECONDHAND_CAP = 0.55

_PRECEDENCE = {
    HOLDER_USER: 3,
    HOLDER_NONE: 2,
    HOLDER_ASSISTANT: 2,
    HOLDER_EXTERNAL: 1,
}
_PERSON_PRECEDENCE = 1


def is_person(holder: str) -> bool:
    return holder.startswith(HOLDER_PERSON_PREFIX) if holder else False


def person_entity_id(holder: str) -> str:
    if not is_person(holder):
        return ""
    return holder.removeprefix(HOLDER_PERSON_PREFIX)


def normalize_holder(holder: object) -> str:
    if isinstance(holder, str):
        cleaned = holder.strip().lower()
        if cleaned in HOLDERS:
            return cleaned
        prefix, separator, identity = cleaned.partition(":")
        if (
            separator
            and prefix + separator == HOLDER_PERSON_PREFIX
            and identity.strip()
        ):
            return HOLDER_PERSON_PREFIX + identity.strip()
    return HOLDER_NONE


class _ClaimSource:
    def __init__(self, holder):
        self.holder = normalize_holder(holder)

    @property
    def ceiling(self):
        limits = {HOLDER_NONE: 1.0, HOLDER_EXTERNAL: SECONDHAND_CAP}
        return limits.get(self.holder, SELF_REPORT_CAP)

    @property
    def authority(self):
        return (
            _PERSON_PRECEDENCE
            if is_person(self.holder)
            else _PRECEDENCE.get(self.holder, _PRECEDENCE[HOLDER_NONE])
        )

    def phrase(self, entity_name):
        labels = {
            HOLDER_NONE: "",
            HOLDER_USER: "you say",
            HOLDER_ASSISTANT: "I concluded",
            HOLDER_EXTERNAL: "reported externally",
        }
        if self.holder in labels:
            return labels[self.holder]
        display = entity_name.strip() or person_entity_id(self.holder)
        return f"{display} believes"


def weight_cap(holder: str) -> float:
    source = _ClaimSource(holder)
    return source.ceiling


def normalize_weight(holder: str, weight: object) -> float:
    import math

    try:
        numeric = float(weight)
    except (TypeError, ValueError):
        numeric = weight_cap(holder)
    if not math.isfinite(numeric):
        numeric = weight_cap(holder)
    bounded = max(0.0, min(1.0, numeric))
    steps = round(bounded / WEIGHT_QUANTUM)
    quantized = round(steps * WEIGHT_QUANTUM, 2)
    return min(quantized, weight_cap(holder))


def precedence(holder: object) -> int:
    source = _ClaimSource(holder)
    return source.authority


def attribution(holder: object, *, entity_name: str = "") -> str:
    return _ClaimSource(holder).phrase(entity_name)


def render_fact_line(
    key: str,
    value_str: str,
    *,
    holder: object = "",
    weight: object = None,
    entity_name: str = "",
) -> str:
    normalized = normalize_holder(holder)
    result = f"{key}: {value_str}"
    if normalized:
        label = attribution(normalized, entity_name=entity_name)
        supplied = weight_cap(normalized) if weight is None else weight
        strength = normalize_weight(normalized, supplied)
        result += f" [{label}, weight {strength:.2f}]"
    return result


def entity_names_for(holders: Iterable[str], graph: Any) -> dict[str, str]:
    requested = set(filter(is_person, holders))
    if not requested:
        return {}
    try:
        entities = graph.entities()
    except Exception:
        return {}
    names = {entity.id: entity.name for entity in entities}
    resolved = ((holder, names.get(person_entity_id(holder))) for holder in requested)
    return {holder: name for holder, name in resolved if name}

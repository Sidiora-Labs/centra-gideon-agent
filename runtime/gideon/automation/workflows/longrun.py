"""Long-run mechanics: the difference between a watcher that demos and one that runs for months.

An `until_cancelled` watcher looks trivial — loop, wait, synthesize — and degrades within days
for three reasons this module addresses:

**Re-processing.** Iteration 40 re-reads everything iterations 1-39 already saw, pays for it
again, and re-synthesizes conclusions it already reached. The fix is a persistent **seen-set**
keyed on stable item identity, journaled rather than held in memory so it survives resume and
restart. Held in memory it would reset on every gateway restart, which is exactly when a
long-running watcher is most likely to be interrupted.

**Unbounded sibling context.** `{{siblings.main-work.output}}` naively means "every output the
sibling ever produced", so cycle 50's prompt carries 50 cycles of findings. Every cycle costs
more than the last, and the cost is superlinear in run length. So the sibling view is
**windowed and significance-filtered by default**, with `| full` as the explicit opt-out. The
default is the safe one because the failure is invisible: nothing breaks, the run just gets
slower and more expensive until it hits a context limit hours in.

**Self-synthesis drift.** A watcher whose synthesis output feeds its own next input compounds
its own paraphrases. `reflection_count` with an eligibility ceiling bounds the lineage depth.

Everything here is pure over explicit state. The controller owns the writes; this module owns
the decisions, which is what makes them testable without an engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SYNTHESIS_WINDOW = 20

DEFAULT_SIGNIFICANCE_THRESHOLD = 0.7

SIBLING_PAYLOAD_CAP_CHARS = 2000

MAX_REFLECTION_COUNT = 3

DIVERSITY_FLOOR = 0.7

MIN_ADAPTIVE_DELAY_SECS = 30
MAX_ADAPTIVE_DELAY_SECS = 24 * 60 * 60

_SIGNIFICANCE_WORDS = {
    "critical": 1.0,
    "high": 0.9,
    "significant": 0.8,
    "medium": 0.6,
    "moderate": 0.6,
    "normal": 0.5,
    "low": 0.3,
    "minor": 0.2,
    "trivial": 0.1,
    "noise": 0.0,
}


def item_guid(item: Any) -> str:
    identity = _ItemProjection(item).identity()
    return _hash(identity) if identity else ""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class SeenSet:
    """Per-watcher record of what has already been processed.

    Ordered, and pruned oldest-first at `capacity`, so a watcher running for months has a
    bounded state footprint. Order is kept because eviction order matters: dropping a RECENT
    guid means the very next cycle re-processes it, which is the one case the seen-set exists
    to prevent.

    `capacity` is generous rather than tight: re-processing one old item costs a few tokens,
    while a bounded set that evicts too eagerly reintroduces the whole problem.
    """

    guids: list[str] = field(default_factory=list)
    capacity: int = 5000

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            self.capacity = 1
        self._index: set[str] = set(self.guids)

    def __contains__(self, guid: str) -> bool:
        return guid in self._index

    def __len__(self) -> int:
        return len(self.guids)

    def mark(self, guid: str) -> bool:
        if not guid or guid in self._index:
            return False
        self.guids.append(guid)
        self._index.add(guid)
        if isinstance(self.capacity, int) and self.capacity >= 0:
            excess = max(0, len(self.guids) - self.capacity)
            self._index.difference_update(self.guids[:excess])
            del self.guids[:excess]
        else:
            while len(self.guids) > self.capacity:
                self._index.discard(self.guids.pop(0))
        return True

    def unseen(self, items: list[Any]) -> list[Any]:
        candidates: dict[str, Any] = {}
        for item in items:
            identity = item_guid(item)
            if identity and identity not in self._index:
                candidates.setdefault(identity, item)
        return list(candidates.values())

    def mark_all(self, items: list[Any]) -> int:
        return sum(1 for item in items if self.mark(item_guid(item)))

    def to_dict(self) -> dict[str, Any]:
        return {"guids": list(self.guids), "capacity": self.capacity}

    @classmethod
    def from_dict(cls, data: Any) -> SeenSet:
        restored = cls()
        if isinstance(data, dict):
            raw_ids, capacity = data.get("guids"), data.get("capacity")
            if isinstance(raw_ids, list):
                restored.guids = list(
                    map(str, filter(lambda value: isinstance(value, str), raw_ids))
                )
            if isinstance(capacity, int) and capacity > 0:
                restored.capacity = int(capacity)
            restored._index = set(restored.guids)
        return restored


def significance_of(item: Any) -> float:
    return _ItemProjection(item).significance()


def sibling_view(
    outputs: list[Any],
    *,
    window: int = DEFAULT_SYNTHESIS_WINDOW,
    threshold: float = DEFAULT_SIGNIFICANCE_THRESHOLD,
    seen: SeenSet | None = None,
    full: bool = False,
) -> list[Any]:
    return _SiblingSelection(outputs, seen).project(
        window=window, threshold=threshold, full=full
    )


def _flatten_outputs(outputs: list[Any]) -> list[Any]:
    return list(_SiblingSelection.expand(outputs))


def compress_payload(
    value: Any, *, cap: int = SIBLING_PAYLOAD_CAP_CHARS
) -> tuple[str, bool]:
    rendered = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    if cap <= 0 or len(rendered) <= cap:
        return rendered, False
    excerpt, omitted = rendered[:cap], len(rendered) - cap
    return excerpt + f"\n… [{omitted} chars truncated — this view is INCOMPLETE]", True


def pairwise_diversity(items: list[Any]) -> float:
    from itertools import combinations

    texts = [_text_of(item) for item in items]
    groups = [set(re.findall(r"[a-z0-9]{3,}", text.lower())) for text in texts]
    groups = [group for group in groups if group]
    distances = (
        1.0 - (len(left & right) / len(left | right) if left | right else 0.0)
        for left, right in combinations(groups, 2)
    )
    total = 0.0
    count = 0
    for distance in distances:
        total += distance
        count += 1
    return round(total / count, 4) if count else 1.0


def convergence_warning(items: list[Any], *, confidence: float = 0.0) -> str:
    if len(items) < 2:
        return ""
    diversity = pairwise_diversity(items)
    message = f"sources converged early / possible echo (diversity {diversity:.2f} < {DIVERSITY_FLOOR}) — corroboration here may be one source repeated"
    return "" if diversity >= DIVERSITY_FLOOR or confidence >= 0.85 else message


def reflection_eligible(item: Any, *, ceiling: int = MAX_REFLECTION_COUNT) -> bool:
    return not isinstance(item, dict) or _ItemProjection(item).reflection() < max(
        1, ceiling
    )


def bump_reflection(item: Any) -> Any:
    return (
        dict(item, reflection_count=_ItemProjection(item).reflection() + 1)
        if isinstance(item, dict)
        else item
    )


def clamp_delay(
    proposed: Any,
    *,
    default: int,
    minimum: int = MIN_ADAPTIVE_DELAY_SECS,
    maximum: int = MAX_ADAPTIVE_DELAY_SECS,
) -> tuple[int, str]:
    if isinstance(proposed, bool) or proposed is None:
        return default, "not_proposed"
    try:
        seconds = int(float(proposed))
    except (TypeError, ValueError):
        return default, "unparseable"
    bounds = (
        (minimum, "up", lambda: seconds < minimum),
        (maximum, "down", lambda: seconds > maximum),
    )
    for bound, direction, outside in bounds:
        if outside():
            return bound, f"clamped_{direction}_from_{seconds}"
    return seconds, ""


@dataclass
class BufferState:
    """Volume-driven synthesis trigger.

    The alternative to wall-clock cadence: synthesize when the buffer FILLS, with a stale-flush
    path so a slow trickle still gets consolidated eventually. A quiet week costs zero LLM
    calls; a busy hour synthesizes promptly. Wall-clock cadence gets exactly this backwards.
    """

    items: list[Any] = field(default_factory=list)
    seal_threshold: int = 20
    seal_tokens: int = 0
    flush_stale_after_secs: int = 3600
    last_flush_at: float = 0.0

    def add(self, items: list[Any]) -> None:
        self.items.extend(items)

    def approx_tokens(self) -> int:
        text = json.dumps(self.items, ensure_ascii=False, default=str)
        return len(text) // 4

    def should_seal(self, *, now: float) -> tuple[bool, str]:
        reason = next(_BufferPolicy(self).reasons(now), "")
        return bool(reason), reason

    def drain(self, *, now: float) -> list[Any]:
        drained = list(self.items)
        self.items = []
        self.last_flush_at = now
        return drained

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": list(self.items),
            "seal_threshold": self.seal_threshold,
            "seal_tokens": self.seal_tokens,
            "flush_stale_after_secs": self.flush_stale_after_secs,
            "last_flush_at": self.last_flush_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> BufferState:
        if not isinstance(data, dict):
            return cls()
        raw_items = data.get("items")
        values = {"items": list(raw_items) if isinstance(raw_items, list) else []}
        for name, default in (
            ("seal_threshold", 20),
            ("seal_tokens", 0),
            ("flush_stale_after_secs", 3600),
        ):
            values[name] = _int(data.get(name), default)
        values["last_flush_at"] = float(data.get("last_flush_at") or 0.0)
        return cls(**values)


CONTINUITY_SUMMARY_LINES = 5
CONTINUITY_TOPIC_CAP = 10
CONTINUITY_REF_CAP = 20
CONTINUITY_LINE_CHARS = 200


def roll_continuity(
    prior: Any, *, outcome: str, topics: list[str], refs: list[str]
) -> dict:
    return _ContinuityFold(prior).advance(outcome, topics, refs)


def continuity_header(continuity: Any) -> str:
    if not isinstance(continuity, dict):
        return ""
    return _ContinuityFold(continuity).header()


def _dedup_capped(fresh: list[str], prior: Any, cap: int) -> list[str]:
    carried = (
        [str(value) for value in prior if isinstance(value, str)]
        if isinstance(prior, list)
        else []
    )
    merged: dict[str, str] = {}
    for value in [str(value) for value in fresh] + carried:
        stripped = value.strip()
        identity = stripped.lower()
        if identity and identity not in merged:
            merged[identity] = stripped[:CONTINUITY_LINE_CHARS]
    return list(merged.values())[:cap]


MIN_TITLE_WORDS = 3


def web_hygiene(
    items: list[Any],
    *,
    allow_domains: list[str] | None = None,
    min_title_words: int = MIN_TITLE_WORDS,
) -> list[Any]:
    policy = _WebItemPolicy(allow_domains, min_title_words)
    return [item for item in items if policy.accept(item)]


def _domain_allowed(url: str, allowed: list[str]) -> bool:
    if not url:
        return False
    normalized = url.strip().lower()
    scheme = re.match(r"^[a-z]+://", normalized)
    authority = normalized[scheme.end() :] if scheme else normalized
    host = authority.partition("/")[0].partition(":")[0]
    return host in allowed or any(host.endswith("." + domain) for domain in allowed)


def _text_of(item: Any) -> str:
    return _ItemProjection(item).text()


def _int(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    return int(raw)


class _ItemProjection:
    def __init__(self, value: Any):
        self.value = value

    def identity(self) -> str:
        value = self.value
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, dict):
            return json.dumps(value, sort_keys=True, default=str)
        for key in ("guid", "id", "uid", "url", "link"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().lower()
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return str(candidate)
        fields = ("statement", "title", "headline", "summary", "content", "text")
        parts = [str(value.get(key, "") or "").strip().lower() for key in fields]
        return "\x1f".join(filter(None, parts))

    def significance(self) -> float:
        if not isinstance(self.value, dict):
            return 1.0
        score = self.value.get("significance", self.value.get("importance", None))
        if score is None:
            return 1.0
        if isinstance(score, bool):
            return float(score)
        if not isinstance(score, (int, float)):
            return _SIGNIFICANCE_WORDS.get(str(score).strip().lower(), 1.0)
        score = float(score)
        if score > 1.0:
            score /= 100.0
        return max(0.0, min(1.0, score))

    def reflection(self) -> int:
        count = self.value.get("reflection_count", 0)
        return (
            int(count) if isinstance(count, int) and not isinstance(count, bool) else 0
        )

    def text(self) -> str:
        if isinstance(self.value, str):
            return self.value
        if not isinstance(self.value, dict):
            return str(self.value)
        fields = ("statement", "title", "summary", "content", "text", "headline")
        candidates = (self.value.get(key) for key in fields)
        selected = next(
            (value for value in candidates if isinstance(value, str) and value.strip()),
            None,
        )
        return (
            selected
            if selected is not None
            else json.dumps(self.value, sort_keys=True, default=str)
        )


class _SiblingSelection:
    def __init__(self, outputs: list[Any], seen: SeenSet | None):
        self.outputs, self.seen = outputs, seen

    @staticmethod
    def expand(outputs: list[Any]):
        carriers = ("findings", "items", "results", "records", "entries")
        for output in outputs:
            if output is None:
                continue
            if isinstance(output, list):
                yield from output
                continue
            if isinstance(output, dict):
                for key in carriers:
                    payload = output.get(key)
                    if isinstance(payload, list):
                        yield from payload
                        break
                else:
                    yield output
            else:
                yield output

    def project(self, *, window: int, threshold: float, full: bool) -> list[Any]:
        candidates = list(self.expand(self.outputs))
        if self.seen is not None:
            candidates = self.seen.unseen(candidates)
        if full:
            return candidates
        retained = list(
            filter(lambda item: significance_of(item) >= threshold, candidates)
        )
        return retained[-window:] if window > 0 and len(retained) > window else retained


class _BufferPolicy:
    def __init__(self, state: BufferState):
        self.state = state

    def reasons(self, now: float):
        state = self.state
        if not state.items:
            return
        if state.seal_threshold > 0 and len(state.items) >= state.seal_threshold:
            yield f"buffer_full:{len(state.items)}_items"
            return
        if state.seal_tokens > 0 and state.approx_tokens() >= state.seal_tokens:
            yield f"buffer_full:{state.approx_tokens()}_tokens"
            return
        if state.flush_stale_after_secs > 0 and state.last_flush_at:
            age = now - state.last_flush_at
            if age >= state.flush_stale_after_secs:
                yield f"flush_stale:{int(age)}s"


class _ContinuityFold:
    def __init__(self, prior: Any):
        self.prior = prior if isinstance(prior, dict) else {}

    def advance(self, outcome: str, topics: list[str], refs: list[str]) -> dict:
        raw = self.prior.get("summary")
        summary = (
            [str(value) for value in raw if isinstance(value, str)]
            if isinstance(raw, list)
            else []
        )
        if outcome.strip():
            summary[0:0] = [outcome.strip()[:CONTINUITY_LINE_CHARS]]
        record = {"summary": summary[:CONTINUITY_SUMMARY_LINES]}
        for field, fresh, cap in (
            ("recent_topics", topics, CONTINUITY_TOPIC_CAP),
            ("recent_refs", refs, CONTINUITY_REF_CAP),
        ):
            record[field] = _dedup_capped(fresh, self.prior.get(field), cap)
        return record

    def header(self) -> str:
        sections = {}
        for key in ("summary", "recent_topics"):
            sections[key] = [
                str(value)
                for value in (self.prior.get(key) or [])
                if str(value).strip()
            ]
        if not any(sections.values()):
            return ""
        lines = ["Context from previous runs — avoid repeating, build on prior work:"]
        lines += list(map(lambda value: "- " + value, sections["summary"]))
        if sections["recent_topics"]:
            lines += ["Recently covered: " + ", ".join(sections["recent_topics"])]
        return "\n".join(lines)


class _WebItemPolicy:
    def __init__(self, domains: list[str] | None, min_words: int):
        self.domains = [
            domain.strip().lower().lstrip(".")
            for domain in (domains or [])
            if str(domain).strip()
        ]
        self.min_words = min_words

    def accept(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return isinstance(item, str) and len(item.split()) >= self.min_words
        title = str(item.get("title", item.get("headline", "")) or "")
        if title and len(title.split()) < self.min_words:
            return False
        if not self.domains:
            return True
        return _domain_allowed(
            str(item.get("url", item.get("link", "")) or ""), self.domains
        )

"""Conflict detection at persist time — flag at ingest, not at query.

The reason this runs on the WRITE path rather than the read path: by the time a contradiction
surfaces during retrieval, something has already built on one side of it. A claim that entered
the store unflagged has been retrieved, cited, and folded into a synthesis, and unwinding that
means finding everything downstream. Flagging at ingest costs one deterministic pass per write.

Deterministic detection (zero cost). Two claims sharing a SUBJECT and PREDICATE with different
OBJECTS, or a subject and object with opposite predicates, conflict — no model needed. This is
what §2.1's structured claims exist to make possible: the same test over free text needs an LLM,
over `{subject, predicate, object}` it is a comparison.

**Both claims are kept, always.** A conflict record carries a source-precedence ladder
(`user > compiled > timeline > external`) so a reader knows which to prefer — but the losing
claim stays, with its citation. Silently picking one is how a store becomes confidently wrong:
the discarded claim was evidence, and its absence is unrecoverable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SOURCE_PRECEDENCE = ("user", "compiled", "timeline", "external")

NUMBER_CONFLICT_MIN_SIM = 0.75

MAX_CONFLICT_CANDIDATES = 30

MAX_CONFLICTS_PER_PASS = 10

_OPPOSITE_PREDICATES = (
    ("is", "is not"),
    ("has", "lacks"),
    ("supports", "blocks"),
    ("increases", "decreases"),
    ("enables", "prevents"),
    ("requires", "forbids"),
    ("includes", "excludes"),
    ("causes", "prevents"),
)

_AUXILIARIES = frozenset(
    {
        "do",
        "doe",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "will",
        "would",
        "can",
        "could",
    }
)

_NEGATIONS = frozenset(
    {
        "not",
        "no",
        "never",
        "cannot",
        "without",
        "lacks",
        "fails",
        "isn't",
        "doesn't",
        "won't",
    }
)


@dataclass
class Claim:
    """One structured assertion. The shape §2.1 persists inside `file_metadata`."""

    id: str = ""
    statement: str = ""
    subject: str = ""
    predicate: str = ""
    object: str = ""
    confidence: float = 0.0
    source_ref: str = ""
    origin: str = "external"

    @classmethod
    def from_dict(cls, data: Any) -> Claim:
        if not isinstance(data, dict):
            return cls()
        claim = cls(
            id=str(data.get("id", "") or ""),
            statement=str(data.get("statement", "") or ""),
            subject=str(data.get("subject", "") or ""),
            predicate=str(data.get("predicate", "") or ""),
            object=str(data.get("object", "") or ""),
            confidence=_float(data.get("confidence"), 0.0),
            source_ref=str(data.get("source_ref", "") or ""),
            origin=str(data.get("origin", "") or "external"),
        )
        if not (claim.subject and claim.predicate):
            claim.subject, claim.predicate, claim.object = decompose(claim.statement)
        return claim

    @property
    def spo(self) -> tuple[str, str, str]:
        return (_norm(self.subject), _norm(self.predicate), _norm(self.object))


@dataclass
class Conflict:
    """A recorded disagreement. First-class, not a log line.

    `prefer` names which side the precedence ladder favours — advice for a reader, never an
    instruction to delete the other. Conflicts record deterministic findings; model review
    proposals are represented separately as inferred edges.
    """

    left_claim: str = ""
    right_claim: str = ""
    left_item: str = ""
    right_item: str = ""
    kind: str = "value"
    prefer: str = ""
    detail: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_claim": self.left_claim,
            "right_claim": self.right_claim,
            "left_item": self.left_item,
            "right_item": self.right_item,
            "kind": self.kind,
            "prefer": self.prefer,
            "detail": self.detail,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class ConflictCandidate:
    """One nearby pair the deterministic pass could not settle.

    Candidates are deliberately a different type from :class:`Conflict`: proximity is enough
    to ask the model a question, but it is not evidence that the two claims disagree.  Keeping
    that distinction in the payload prevents a review surface from presenting every shortlisted
    neighbour as an already-established conflict.
    """

    left_claim: str = ""
    right_claim: str = ""
    left_item: str = ""
    right_item: str = ""
    similarity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_claim": self.left_claim,
            "right_claim": self.right_claim,
            "left_item": self.left_item,
            "right_item": self.right_item,
            "basis": "unsettled",
            "similarity": round(self.similarity, 4),
        }


_PREDICATE_RE = re.compile(
    r"^(?P<subject>.{1,80}?)\s+"
    r"(?P<predicate>is not|are not|is|are|was|were|has|have|had|lacks|requires|forbids|"
    r"supports|blocks|enables|prevents|causes|increases|decreases|includes|excludes|"
    r"takes|uses|needs|returns|measured|costs)\s+"
    r"(?P<object>.+)$",
    re.IGNORECASE,
)


def decompose(statement: str) -> tuple[str, str, str]:
    """Best-effort `(subject, predicate, object)` from a prose claim.

    Returns empty strings when nothing matches, and the deterministic tier then declines to
    judge rather than guessing — an unparsed claim is not a claim about nothing.
    """
    text = " ".join((statement or "").split())
    if not text:
        return ("", "", "")
    match = _PREDICATE_RE.match(text)
    if not match:
        return ("", "", "")
    return (
        match.group("subject").strip(),
        match.group("predicate").strip().lower(),
        match.group("object").strip().rstrip(".!?"),
    )


def deterministic_conflict(left: Claim, right: Claim) -> Conflict | None:
    """Do these two claims conflict, provably, with no model call?

    Three shapes, all requiring the same SUBJECT — without that, "X is fast" and "Y is slow"
    would read as a contradiction, and a store full of false conflicts is worse than one with
    none because nobody reads the report.
    """
    ls, lp, lo = left.spo
    rs, rp, ro = right.spo

    if polarity(left.statement) != polarity(right.statement):
        if core_similarity(left.statement, right.statement) >= NUMBER_CONFLICT_MIN_SIM:
            return _make(
                left, right, kind="polarity", detail="opposite polarity, same claim"
            )

    if not ls or not rs or ls != rs:
        return None
    if not lp or not rp:
        return None

    if lp == rp and lo and ro and lo != ro:
        if similarity(left.statement, right.statement) < NUMBER_CONFLICT_MIN_SIM:
            return None
        kind = (
            "number"
            if _numbers(lo) and _numbers(ro) and _numbers(lo) != _numbers(ro)
            else "value"
        )
        return _make(
            left,
            right,
            kind=kind,
            detail=f"{left.predicate or lp}: {left.object or lo} vs {right.object or ro}",
        )

    if lo and ro and lo == ro and _opposed(lp, rp):
        return _make(left, right, kind="polarity", detail=f"{lp} vs {rp} on {lo}")

    return None


def find_conflicts(incoming: list[Claim], existing: list[Claim]) -> list[Conflict]:
    """Every deterministic conflict between what is arriving and what is stored.

    Incoming-vs-existing only, NOT incoming-vs-incoming: two claims in one write came from one
    source that already reconciled them, and flagging them would report the source's own
    internal structure as a disagreement.
    """
    out: list[Conflict] = []
    for new in incoming:
        for old in existing:
            if new.id and new.id == old.id:
                continue
            conflict = deterministic_conflict(new, old)
            if conflict is not None:
                out.append(conflict)
                if len(out) >= MAX_CONFLICTS_PER_PASS:
                    return out
    return out


def _make(left: Claim, right: Claim, *, kind: str, detail: str) -> Conflict:
    return Conflict(
        left_claim=left.statement,
        right_claim=right.statement,
        left_item=left.source_ref,
        right_item=right.source_ref,
        kind=kind,
        prefer=prefer_side(left, right),
        detail=detail,
        confidence=1.0,
    )


def prefer_side(left: Claim, right: Claim) -> str:
    """Which side the source-precedence ladder favours, or "" when it cannot say.

    "" is a real answer and the honest one for two same-tier sources: a ladder that always
    picked a winner would manufacture authority out of arrival order.
    """
    ranks = {name: index for index, name in enumerate(SOURCE_PRECEDENCE)}
    left_rank = ranks.get(_norm(left.origin), len(SOURCE_PRECEDENCE))
    right_rank = ranks.get(_norm(right.origin), len(SOURCE_PRECEDENCE))
    if left_rank < right_rank:
        return "left"
    if right_rank < left_rank:
        return "right"
    return ""


def shortlist(
    incoming: Claim, existing: list[Claim], *, cap: int = MAX_CONFLICT_CANDIDATES
) -> list[Claim]:
    """Candidates worth one model call, ranked by overlap.

    Deterministic shortlisting BEFORE the call is what keeps the marginal cost
    graph-size-independent: without it, every write would send the whole store.
    """
    scored = [
        (similarity(incoming.statement, other.statement), index, other)
        for index, other in enumerate(existing)
        if other.statement and other.source_ref != incoming.source_ref
    ]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [claim for score, _index, claim in scored[:cap] if score > 0]


def unsettled_candidates(
    incoming: list[Claim], existing: list[Claim]
) -> list[ConflictCandidate]:
    """Nearby incoming/stored pairs not decided by the deterministic tier.

    This is the hand-off contract between the zero-token write pass and a model reviewer.  A
    deterministic conflict is omitted because it is already settled; a zero-overlap neighbour is
    omitted because asking a model about every stored claim would make write cost grow with the
    library.  The total, not merely each incoming claim's shortlist, is capped.
    """
    out: list[ConflictCandidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for new in incoming:
        for old in shortlist(new, existing):
            if new.id and new.id == old.id:
                continue
            if deterministic_conflict(new, old) is not None:
                continue
            key = (new.id, old.id, new.statement, old.statement)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ConflictCandidate(
                    left_claim=new.statement,
                    right_claim=old.statement,
                    left_item=new.source_ref,
                    right_item=old.source_ref,
                    similarity=similarity(new.statement, old.statement),
                )
            )
            if len(out) >= MAX_CONFLICT_CANDIDATES:
                return out
    return out


RELATION_VERBS = ("supersedes", "contradicts", "derived_from", "depends_on", "part_of")

MAX_EDGES_PER_PASS = 10


@dataclass
class Edge:
    """One typed relation between two items."""

    source: str
    target: str
    relation: str
    confidence: float = 1.0
    provenance: str = "extracted"
    justification: str = ""

    @property
    def valid(self) -> bool:
        """A self-edge is never meaningful, and an unknown verb is unreadable downstream."""
        return bool(
            self.source
            and self.target
            and self.source != self.target
            and self.relation in RELATION_VERBS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "confidence": round(self.confidence, 4),
            "provenance": self.provenance,
            "justification": self.justification[:120],
        }


def edges_from_conflicts(conflicts: list[Conflict]) -> list[Edge]:
    """Extracted `contradicts` edges for deterministic findings."""
    out: list[Edge] = []
    for conflict in conflicts:
        if not (conflict.left_item and conflict.right_item):
            continue
        edge = Edge(
            source=conflict.left_item,
            target=conflict.right_item,
            relation="contradicts",
            confidence=conflict.confidence,
            provenance="extracted",
            justification=conflict.detail,
        )
        if edge.valid:
            out.append(edge)
    return out[:MAX_EDGES_PER_PASS]


def parse_edge_proposals(raw: Any, *, source_item: str) -> list[Edge]:
    """Typed edges a background model proposed, validated against the closed vocabulary."""
    if not isinstance(raw, dict):
        return []
    rows = raw.get("edges")
    if not isinstance(rows, list):
        return []
    out: list[Edge] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        edge = Edge(
            source=source_item,
            target=str(row.get("target", "") or ""),
            relation=str(row.get("relation", "") or "").strip().lower(),
            confidence=min(0.95, max(0.1, _float(row.get("confidence"), 0.5))),
            provenance="inferred",
            justification=str(row.get("justification", "") or ""),
        )
        if edge.valid:
            out.append(edge)
    return out[:MAX_EDGES_PER_PASS]


def similarity(left: str, right: str) -> float:
    """Token-overlap similarity in [0, 1].

    Jaccard rather than embeddings because this runs on EVERY persist, and the no-embedder path
    is a supported configuration — a conflict pass that silently degraded to "nothing conflicts"
    there would fail exactly where nobody is watching.
    """
    a = set(re.findall(r"[a-z0-9]{2,}", (left or "").lower()))
    b = set(re.findall(r"[a-z0-9]{2,}", (right or "").lower()))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def core_similarity(left: str, right: str) -> float:
    """Similarity with the NEGATION stripped and inflection flattened.

    Plain Jaccard is the wrong instrument for a polarity comparison: negating a claim adds
    tokens ("does", "not") and usually changes inflection ("needs" → "need"), so the score is
    systematically depressed for exactly the pair the rule wants to catch. Measured, "the gateway
    needs a restart after a config change" vs "the gateway does not need a restart after a config
    change" scored 0.60 against a 0.75 floor and was missed.

    Removing the negation words is what makes the floor mean "are these about the same thing"
    rather than "does one of them contain a negation".
    """
    return similarity(_strip_negation(left), _strip_negation(right))


def _strip_negation(statement: str) -> str:
    """Drop negation and auxiliary words, and flatten a trailing `s`.

    The auxiliaries go too ("does not need" leaves `do` and `need` behind otherwise), and the
    crude de-pluralization handles the verb agreement a negation forces. Crude on purpose: a real
    stemmer would collapse words this comparison needs kept distinct.
    """
    tokens = re.findall(r"[a-z0-9]{2,}", (statement or "").lower())
    kept = [
        token.rstrip("s") if len(token) > 3 else token
        for token in tokens
        if token not in _NEGATIONS and token not in _AUXILIARIES
    ]
    return " ".join(kept)


def polarity(statement: str) -> bool:
    """True when the statement asserts, False when it negates.

    Counts negation WORDS rather than testing for any, so a double negation ("it is not never
    used") reads as an assertion instead of a denial. Negative PREFIXES are deliberately not
    detected — "not unrelated" reads as a denial here. Morphological negation needs a lexicon to
    do correctly, and a half-built one that treats "invaluable" as negated would be worse than
    the miss: it would manufacture conflicts between statements that agree.
    """
    tokens = re.findall(r"[a-z']+", (statement or "").lower())
    return sum(1 for token in tokens if token in _NEGATIONS) % 2 == 0


def _opposed(left: str, right: str) -> bool:
    for first, second in _OPPOSITE_PREDICATES:
        if {left, right} == {first, second}:
            return True
    return False


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text or "")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).split())


def _float(raw: Any, fallback: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return fallback
    return float(raw)

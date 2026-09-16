"""LexiconService (core LEX) — the vocabulary engine over the LexiconStore.

Owns the four behaviors the design locks:
  * ``rebuild_from_graph`` — sync terms from knowledge-graph entities (name + aliases +
    entity_type), computing Double Metaphone keys. Incremental-friendly (upsert by id).
  * ``select_bias_terms``  — a ranked, budget-capped term list for PRE-decode biasing
    (LEX.3): context entities first (a meeting's own notes prime its audio), then global
    top-weighted top-ups.
  * ``correct``            — POST-decode phonetic correction of a TranscriptResult (LEX.4):
    fires only when it SOUNDS like a Lexicon term, is SPELLED differently, and the source
    word is low-confidence; hybrid policy = auto-apply learned/high-confidence, propose the
    rest.
  * ``learn_correction``   — the feedback loop (LEX.5): upsert heard→meant, raise the
    term's weight, flip auto_apply past threshold.

A module-level ``select_bias_terms`` async wrapper is the seam the TranscriptionNode calls
(so the node needs no service handle); it returns [] when the Lexicon is empty/disabled.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

from gideon.cognition.lexicon.phonetics import phonetic_keys
from gideon.cognition.lexicon.store import LexiconStore
from gideon.cognition.lexicon.vocabulary_flow import (
    BiasQueue,
    KnowledgeVocabulary,
    PhoneticCandidates,
    TermSync,
    TranscriptPass,
)

logger = logging.getLogger(__name__)

_BIAS_BUDGET = 64
_STOP_WORDS = frozenset(
    "the a an and or but if then this that these those is are was were be been being have "
    "has had do does did will would can could should may might must to of in on at for with "
    "as by from up out so no not yes it its he she they we you i me my your our their".split()
)
_LOW_PROB = 0.6


def _prefix_match(a: str, b: str, min_len: int = 3) -> bool:
    """True if one metaphone key is a prefix of the other (both ≥ min_len). Catches a
    truncating mishearing whose key is a shortened form of the real term's key."""
    if len(a) < min_len:
        return False
    if len(b) < min_len:
        return False
    common = min(len(a), len(b))
    return len(a) != len(b) and a[:common] == b[:common]


@dataclass
class Correction:
    start: float
    end: float
    heard: str
    suggested: str
    score: float


@dataclass
class CorrectionOutcome:
    applied: list[Correction] = field(default_factory=list)
    suggested: list[Correction] = field(default_factory=list)


class LexiconService:
    def __init__(self, store: LexiconStore | None = None):
        self.store = store or LexiconStore()

    def rebuild_from_graph(self, entities: list[dict]) -> int:
        """Sync the Lexicon's graph-sourced terms from a list of entity dicts
        (``{id, name, entity_type, aliases}``). Returns the number of terms upserted.
        A true resync: graph terms whose entity no longer exists are pruned, and a
        user-disabled (pruned) graph term stays disabled. Manual/learned terms are
        untouched (upsert_term won't downgrade their source)."""
        return TermSync(sys.modules[__name__], self).reconcile(entities)

    def add_manual_term(
        self,
        canonical: str,
        *,
        aliases: list[str] | None = None,
        entity_type: str = "manual",
    ) -> str:
        return TermSync(sys.modules[__name__], self).manual(
            canonical, aliases, entity_type
        )

    def select_bias_terms(
        self, *, context_terms: list[str] | None = None, budget: int = _BIAS_BUDGET
    ) -> list[str]:
        """Ranked, budget-capped bias terms. Context terms (e.g. a meeting's sibling
        entities) come FIRST, then globally top-weighted terms fill the rest."""
        return BiasQueue.select(self.store, context_terms, budget)

    def correct(self, result) -> CorrectionOutcome:
        """Correct mis-heard terms in a TranscriptResult in place (auto-apply branch) +
        collect proposals (propose branch). ``result`` is an stt.provider.TranscriptResult.

        For each word: if it sounds like a Lexicon term but is spelled differently and
        isn't a common word, and (learned auto-correction OR low source confidence), rewrite
        it (auto) or attach a suggestion (propose). Timestamps are preserved."""
        return TranscriptPass(sys.modules[__name__], self).run(result)

    def _best_phonetic_match(self, word: str) -> tuple[str, float] | None:
        """Return (canonical, score) of the best same-sound Lexicon term, or None. Score
        blends phonetic-key overlap with a literal-difference bonus (sounds same, spelled
        different is the strongest signal)."""
        return PhoneticCandidates(sys.modules[__name__], self.store, word).best()

    def learn_correction(
        self, heard: str, meant: str, *, always: bool = False, threshold: int = 2
    ) -> None:
        """Record a user transcript fix: upsert heard→meant, raise the term's weight so it's
        more likely biased next time, and (past threshold / 'always') flip auto_apply.
        """
        TermSync(sys.modules[__name__], self).learn(heard, meant, always, threshold)

    def list_terms(self, **kw):
        return self.store.list_terms(**kw)

    def list_corrections(self, **kw):
        return self.store.list_corrections(**kw)


async def select_bias_terms(
    *, context_item_id: str | None = None, budget: int = _BIAS_BUDGET
) -> list[str]:
    """The node-facing entry point (LEX.3). Resolves context entities for the item's
    siblings when available, else falls back to globally top-weighted terms. Returns []
    when the Lexicon is empty/unavailable so transcription just runs unbiased."""
    return BiasQueue.for_item(sys.modules[__name__], context_item_id, budget)


def _context_terms_for_item(item_id: str) -> list[str]:
    """Entity names related to *item_id* (its own extracted entities), for context-scoped
    biasing. Best-effort — returns [] on any failure."""
    return KnowledgeVocabulary.related(item_id)


_service: LexiconService | None = None


def get_lexicon_service() -> LexiconService:
    global _service
    if _service is None:
        _service = LexiconService()
    return _service

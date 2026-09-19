"""Term reconciliation, ordered decoder vocabulary and transcription decisions."""

from dataclasses import dataclass
from itertools import chain
from typing import Any


class TermSync:
    def __init__(self, api, service):
        self.api = api
        self.service = service

    def keys(self, surfaces):
        tokens = chain.from_iterable(str(surface).split() for surface in surfaces)
        return list(
            chain.from_iterable(self.api.phonetic_keys(token) for token in tokens)
        )

    def reconcile(self, entities):
        retained, written = set(), 0
        for entity in entities:
            canonical = (entity.get("name") or "").strip()
            if not canonical:
                continue
            aliases = entity.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            keys = self.keys([canonical, *aliases])
            identity = f"graph_{entity.get('id') or canonical.lower()}"
            attributes = dict(
                term_id=identity,
                canonical=canonical,
                aliases=[str(alias) for alias in aliases],
                phonetic_keys=sorted(set(keys)),
                entity_type=str(entity.get("entity_type") or ""),
                weight=1.0,
                source="graph",
            )
            self.service.store.upsert_term(**attributes)
            retained.add(identity)
            written += 1
        removed = self.service.store.prune_graph_terms(keep=retained)
        if removed:
            self.api.logger.info(
                "lexicon rebuild: pruned %d stale graph terms", removed
            )
        return written

    def manual(self, canonical, aliases, entity_type):
        keys = self.keys([canonical, *(aliases or [])])
        identity = "manual_" + canonical.lower().replace(" ", "_")
        self.service.store.upsert_term(
            term_id=identity,
            canonical=canonical,
            aliases=aliases or [],
            phonetic_keys=sorted(set(keys)),
            entity_type=entity_type,
            weight=2.0,
            source="manual",
        )
        return identity

    def learn(self, heard, meant, always, threshold):
        source, target = heard.strip(), meant.strip()
        if not source or not target or source == target:
            return
        keys = self.api.phonetic_keys(target) or [""]
        self.service.store.upsert_correction(
            source,
            target,
            phonetic_key=keys[0],
            auto_apply=True if always else None,
            threshold=threshold,
        )
        absent = self.service.store.get_term_by_canonical(target) is None
        if absent:
            self.service.add_manual_term(target, entity_type="learned")
        self.service.store.bump_weight(target, delta=1.0)


class BiasQueue:
    @staticmethod
    def local_terms(context):
        for item in context or []:
            text = item.strip()
            if text:
                yield text

    @staticmethod
    def global_terms(store, budget):
        yield from (term.canonical for term in store.top_terms(budget * 2))

    @classmethod
    def select(cls, store, context, budget):
        accepted = {}
        candidates = chain(cls.local_terms(context), cls.global_terms(store, budget))
        for text in candidates:
            key = text.lower()
            if key in accepted:
                continue
            accepted[key] = text
            if len(accepted) >= budget:
                break
        return list(accepted.values())

    @staticmethod
    def for_item(api, item_id, budget):
        return ScopedBiasRequest(item_id, budget).execute(api)


@dataclass(frozen=True)
class ScopedBiasRequest:
    item_id: str | None
    budget: int

    def plans(self, api):
        service = api.get_lexicon_service()
        if service.store.count_terms() != 0:
            arguments = {"context_terms": [], "budget": self.budget}
            if self.item_id:
                arguments["context_terms"] = api._context_terms_for_item(self.item_id)
            yield service, arguments

    def execute(self, api):
        try:
            for service, arguments in self.plans(api):
                return service.select_bias_terms(**arguments)
        except Exception:
            api.logger.debug("select_bias_terms failed (non-fatal)", exc_info=True)
        return []


class PhoneticCandidates:
    def __init__(self, api, store, word):
        from difflib import SequenceMatcher

        self.compare = SequenceMatcher
        self.api, self.store, self.word = api, store, word
        self.keys = api.phonetic_keys(word)

    def exact(self):
        for key in self.keys:
            yield from self.store.terms_for_phonetic_key(key)

    def prefixes(self):
        for key in self.keys:
            for term in self.store.terms_for_phonetic_prefix(key):
                candidates = self.api.phonetic_keys(term.canonical)
                if any(self.api._prefix_match(key, item) for item in candidates):
                    yield term

    def rank(self, candidates, base, difference_weight):
        winner = None
        for term in candidates:
            similarity = self.compare(
                None, self.word.lower(), term.canonical.lower()
            ).ratio()
            raw_score = base + (1.0 - similarity) * difference_weight
            if winner is not None and not raw_score > winner[1]:
                continue
            winner = term.canonical, round(raw_score, 3)
        return winner

    def best(self):
        exact = self.rank(self.exact(), 0.7, 0.3)
        if exact is not None:
            return exact
        return self.rank(self.prefixes(), 0.6, 0.2)


@dataclass(frozen=True)
class WordChange:
    heard: str
    target: str
    score: float
    automatic: bool

    def deliver(self, api, word, outcome):
        if self.automatic:
            word.word = word.word.replace(self.heard, self.target)
        record = api.Correction(
            word.start, word.end, self.heard, self.target, self.score
        )
        destination = outcome.applied if self.automatic else outcome.suggested
        destination.append(record)


class TranscriptPass:
    def __init__(self, api, service):
        self.api, self.service = api, service

    def decision(self, word, learned):
        bare = word.word.strip().strip(".,!?;:\"'()[]").strip()
        if not bare or bare.lower() in self.api._STOP_WORDS or len(bare) < 3:
            return None
        key = bare.lower()
        if key in learned:
            target = learned[key]
            return None if target == bare else WordChange(bare, target, 1.0, True)
        candidate = self.service._best_phonetic_match(bare)
        if candidate is None:
            return None
        target, score = candidate
        if target.lower() == bare.lower():
            return None
        uncertain = (word.prob or 1.0) <= self.api._LOW_PROB
        automatic = score >= 0.9 and uncertain
        if automatic or uncertain:
            return WordChange(bare, target, score, bool(automatic))
        return None

    @staticmethod
    def assemble(result):
        for segment in result.segments:
            if segment.words:
                updated = "".join(word.word for word in segment.words).strip()
                segment.text = updated or segment.text
        if result.segments:
            texts = (segment.text for segment in result.segments if segment.text)
            result.text = " ".join(texts).strip() or result.text

    def run(self, result):
        outcome = self.api.CorrectionOutcome()
        learned = self.service.store.auto_corrections()
        for segment in result.segments:
            for word in segment.words or []:
                change = self.decision(word, learned)
                if change is not None:
                    change.deliver(self.api, word, outcome)
        self.assemble(result)
        return outcome


_ALL_ENTITIES = object()


class KnowledgeVocabulary:
    @staticmethod
    def rows(*, context_item_id=_ALL_ENTITIES):
        from gideon.cognition.knowledge import get_knowledge_store

        store = get_knowledge_store()
        columns: tuple[str, ...] = ("id", "name", "entity_type", "aliases")
        relation = "entities"
        bindings: tuple[Any, ...] = ()
        if context_item_id is not _ALL_ENTITIES:
            columns = ("e.name",)
            relation = "entities e JOIN mentions m ON m.entity_id = e.id WHERE m.item_id = ? LIMIT 100"
            bindings = (context_item_id,)
        query = "SELECT " + ", ".join(columns) + " FROM " + relation
        return store.db.execute(query, bindings)

    @classmethod
    def related(cls, item_id):
        try:
            selected = []
            for record in cls.rows(context_item_id=item_id):
                if record["name"]:
                    selected.append(record["name"])
            return selected
        except Exception:
            return []

    @classmethod
    def snapshot(cls):
        import json

        result = []
        for record in cls.rows():
            try:
                aliases = json.loads(record["aliases"] or "[]")
            except Exception:
                aliases = []
            result.append(
                {
                    "id": record["id"],
                    "name": record["name"],
                    "entity_type": record["entity_type"],
                    "aliases": aliases,
                }
            )
        return result

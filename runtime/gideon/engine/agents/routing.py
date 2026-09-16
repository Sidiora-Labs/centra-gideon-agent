"""Specialist suggestions with ranked evidence, cooldowns and explicit user choice."""

from __future__ import annotations

import heapq
import logging
import math
import re
from dataclasses import dataclass

from gideon.engine.agents.defaults import is_reserved_agent

logger = logging.getLogger(__name__)
_KEYWORD_GATE = 0.7
DEFAULT_MIN_CONFIDENCE = 0.62
_MARGIN = 0.1
_MIN_KEYWORD_PHRASE_WORDS = 3
_STORE = "agent_routing"
_TURNS_BETWEEN_SUGGESTIONS = 5


@dataclass(frozen=True)
class RouteCandidate:
    agent: str
    specialty: str
    score: float
    method: str


def _words(text: str) -> set[str]:
    return {match.group() for match in re.finditer(r"\w+", text.lower())}


def _cosine(a: list[float], b: list[float]) -> float:
    def length(vector):
        return math.sqrt(sum(value * value for value in vector))

    first, second = length(a), length(b)
    product = sum(left * right for left, right in zip(a, b))
    return product / (first * second) if first and second else 0.0


def _keyword_score(query: str, route_hints: str) -> tuple[float, int]:
    if not route_hints.strip():
        return 0.0, 0
    query_words = _words(query)
    phrases = (_words(phrase) for phrase in route_hints.split(","))
    scores = (
        (len(words.intersection(query_words)) / len(words), len(words))
        for words in phrases
        if words
    )
    winner = max(scores, key=lambda score: score[0], default=(0.0, 0))
    return winner if winner[0] > 0 else (0.0, 0)


def eligible_candidates(cfg) -> list[tuple[str, str, str]]:
    candidates = []
    for name, profile in (cfg.agents or {}).items():
        if is_reserved_agent(name):
            continue
        metadata = tuple(
            (getattr(profile, field, "") or "").strip()
            for field in ("specialty", "route_hints")
        )
        if any(metadata):
            candidates.append((name, *metadata))
    return candidates


@dataclass(frozen=True)
class _RoutingScore:
    name: str
    specialty: str
    value: float
    phrase_words: int = 0

    def candidate(self, method):
        return RouteCandidate(self.name, self.specialty, self.value, method)


def _leading_scores(scores):
    if all(math.isfinite(score.value) for score in scores):
        return heapq.nlargest(2, scores, key=lambda score: score.value)
    return sorted(scores, key=lambda score: score.value, reverse=True)[:2]


class _RouteRanking:
    def __init__(self, message, candidates, confidence, cache):
        self.message = message
        self.candidates = candidates
        self.confidence = confidence
        self.cache = cache

    @staticmethod
    def choose(scores, method, confidence, required_words=0):
        ranked = _leading_scores(scores)
        if not ranked:
            return None
        winner = ranked[0]
        runner = ranked[1].value if len(ranked) > 1 else 0.0
        accepted = (
            winner.value >= confidence
            and winner.phrase_words >= required_words
            and winner.value - runner >= _MARGIN
        )
        return winner.candidate(method) if accepted else None

    def resolve(self):
        lexical = []
        for name, specialty, hints in self.candidates:
            value, count = _keyword_score(self.message, hints)
            lexical.append(_RoutingScore(name, specialty, value, count))
        vector, model = _embed(self.message)
        semantic = []
        if vector is not None:
            for name, specialty, hints in self.candidates:
                candidate = _candidate_vector(name, specialty, hints, model, self.cache)
                if candidate is not None:
                    semantic.append(
                        _RoutingScore(name, specialty, _cosine(vector, candidate))
                    )
        return self.choose(semantic, "embedding", self.confidence) or self.choose(
            lexical, "keyword", _KEYWORD_GATE, _MIN_KEYWORD_PHRASE_WORDS
        )


def classify(
    message: str,
    candidates: list[tuple[str, str, str]],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    embed_cache: dict | None = None,
) -> RouteCandidate | None:
    text = (message or "").strip()
    if text and candidates:
        try:
            return _RouteRanking(
                text, candidates, min_confidence, embed_cache
            ).resolve()
        except Exception:
            logger.debug("routing classify failed", exc_info=True)
    return None


def _embed(text: str):
    try:
        from gideon.integrations.embedding_providers.registry import (
            _active_embedding_spec,
            get_active_embed_fn,
        )

        embed = get_active_embed_fn()
    except Exception:
        return None, ""
    if embed is None:
        return None, ""
    try:
        specification = _active_embedding_spec()
        model = f"{specification[0]}:{specification[1]}" if specification else ""
    except Exception:
        model = ""
    try:
        vector = embed(text)
    except Exception:
        vector = None
    return vector, model


@dataclass
class _VectorMemo:
    cache: dict | None
    model: str

    def resolve(self, name, text):
        if self.cache is not None:
            previous = self.cache.get(name)
            if previous and previous[0] == self.model:
                return previous[1]
        vector, _ = _embed(text)
        if self.cache is not None and vector is not None:
            self.cache[name] = (self.model, vector)
        return vector


def _candidate_vector(
    name: str, specialty: str, hints: str, model: str, cache: dict | None
):
    text = (specialty + " " + hints).strip()
    return _VectorMemo(cache, model).resolve(name, text) if text else None


def _load_store() -> dict:
    from gideon.extensions.providers.entity_routes import _load_entity_settings

    try:
        document = _load_entity_settings(_STORE)
    except Exception:
        return {}
    if not isinstance(document, dict):
        return {}
    document.update(
        muted=list(
            dict.fromkeys(str(name).lower() for name in (document.get("muted") or []))
        ),
        dismissals={
            str(name).lower(): entry
            for name, entry in (document.get("dismissals") or {}).items()
        },
    )
    return document


def _save_store(store: dict) -> None:
    from gideon.extensions.providers.entity_routes import _save_entity_settings

    try:
        _save_entity_settings(_STORE, store)
    except Exception:
        logger.debug("agent-routing store save failed", exc_info=True)


class _SuggestionPreferences:
    def __init__(self):
        self.document = _load_store()

    def blocked(self, agent, now, cooldown_hours):
        if agent in (self.document.get("muted") or []):
            return True
        history = (self.document.get("dismissals") or {}).get(agent)
        if not isinstance(history, dict):
            return False
        elapsed = now - float(history.get("last_dismissed_at", 0.0) or 0.0)
        return elapsed < max(0.0, cooldown_hours) * 3600.0

    def dismiss(self, agent, now, mute_at):
        history = self.document.setdefault("dismissals", {})
        entry = history.setdefault(agent, {"count": 0, "last_dismissed_at": 0.0})
        entry.update(count=int(entry.get("count", 0)) + 1, last_dismissed_at=now)
        muted = self.document.setdefault("muted", [])
        if entry["count"] >= mute_at and agent not in muted:
            muted.append(agent)
        _save_store(self.document)
        return {"agent": agent, "count": entry["count"], "muted": agent in muted}

    def restore(self, agent):
        muted = self.document.get("muted") or []
        if agent in muted:
            muted.remove(agent)
            self.document["muted"] = muted
        (self.document.get("dismissals") or {}).pop(agent, None)
        _save_store(self.document)

    def status(self):
        return {
            "muted": list(self.document.get("muted") or []),
            "dismissals": dict(self.document.get("dismissals") or {}),
        }


def is_suppressed(agent: str, *, now: float, cooldown_hours: float) -> bool:
    key = agent.lower()
    return _SuggestionPreferences().blocked(key, now, cooldown_hours)


def record_dismiss(agent: str, *, now: float, mute_at: int = 3) -> dict:
    key = agent.lower()
    return _SuggestionPreferences().dismiss(key, now, mute_at)


def unmute(agent: str) -> None:
    key = agent.lower()
    _SuggestionPreferences().restore(key)


def routing_status() -> dict:
    return _SuggestionPreferences().status()


def _routing_state(state) -> tuple[dict, dict]:
    names = ("_routing_embed_cache", "_routing_last_turn")
    for name in names:
        if not hasattr(state, name):
            setattr(state, name, {})
    return tuple(getattr(state, name) for name in names)


class _SendSuggestion:
    def __init__(self, state, session, config):
        self.state = state
        self.session = session
        self.config = config
        self.policy = config.agents_routing

    def eligible(self):
        if not self.policy.enabled:
            return False
        selected = getattr(self.session, "agent", "") or ""
        default = self.config.default_agent or ""
        return (not selected or selected == default) and getattr(
            self.session, "memory_mode", "persistent"
        ) == "persistent"

    def resolve(self, message):
        import time

        now = time.time()
        cache, previous = _routing_state(self.state)
        session = self.session
        key = getattr(session, "key", "")
        turn = sum(
            message.get("role") == "user"
            for message in getattr(session, "messages", [])
        )
        if (
            turn - previous.get(key, -_TURNS_BETWEEN_SUGGESTIONS)
            < _TURNS_BETWEEN_SUGGESTIONS
        ):
            return None
        selected = getattr(session, "agent", "") or ""
        default = self.config.default_agent or ""
        candidates = [
            row
            for row in eligible_candidates(self.config)
            if row[0] not in (selected, default)
        ]
        if not candidates:
            return None
        suggestion = classify(
            message,
            candidates,
            min_confidence=self.policy.min_confidence,
            embed_cache=cache,
        )
        if suggestion is None or is_suppressed(
            suggestion.agent, now=now, cooldown_hours=self.policy.cooldown_hours
        ):
            return None
        previous[key] = turn
        self.audit(key, suggestion)
        return suggestion

    @staticmethod
    def audit(key, result):
        try:
            from gideon.security.sel import sel

            sel().log_api_access(
                caller="dashboard",
                operation="agents.routing_suggest",
                outcome="suggested",
                source="dashboard",
                resources=f"session={key},agent={result.agent},method={result.method},score={result.score:.3f}",
            )
        except Exception:
            logger.debug("routing SEL log failed", exc_info=True)


def suggest_for_send(state, session, message: str) -> RouteCandidate | None:
    try:
        from gideon.core.config.loader import AppConfig

        suggestion = _SendSuggestion(state, session, AppConfig.load())
        return suggestion.resolve(message) if suggestion.eligible() else None
    except Exception:
        logger.debug("suggest_for_send failed", exc_info=True)
        return None

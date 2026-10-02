"""Select a turn's schema budget and search the complete tool inventory."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from gideon.engine.agents.native.tool_vectors import (
    bound_embedder,
    default_path,
    tool_text,
    tool_vectors,
)
from gideon.core.token_estimate import NOMINAL_CHARS_PER_TOKEN

logger = logging.getLogger(__name__)
DEFAULT_K = 48
DEFAULT_SEMANTIC_THRESHOLD = 0.55
_KEYWORD_GATE = 0.5
SCHEMA_WINDOW_FRACTION = 0.125
_STRUCTURAL_HINTS = (
    (r"https?://|www\.|\.com\b|\.org\b", ("web", "fetch", "url", "browse")),
    (
        r"\bschedul|\bremind|\bcron\b|\bdaily\b|\bweekly\b|\bmonthly\b|\bhourly\b|\bevery\s+(day|week|weekday|hour|minute|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\b(message|text|ping|notify)\s+me\b",
        ("schedule", "cron", "trigger", "onetime", "recurring", "automation_create"),
    ),
    (r"\bwhen(ever)?\b[^.?!\n]{0,80}\b(lands?|arrives?|appears?|changes?|fails?|completes?)\b|\bautomat(e|es|ed|ion|ions|ically)\b", ("automation_create",)),
    (
        r"/|\.py\b|\.ts\b|\.md\b|\bfile\b|\bdirectory\b|\bfolder\b",
        (),
    ),
    (
        r"\bshell\b|\bbash\b|\bcommand\b|\bterminal\b|\bexecute\b|\brun\b|\$\s"
        r"|\b(npm|pip|make|cargo|go|node|python|pytest|ls|cat|echo|chmod|mkdir|curl)\b"
        r"|\bgit\b|commit|diff|branch|stage|\btest|\bpytest|\bspec\b|assert|lint|build",
        ("bash", "shell", "exec", "command", "terminal"),
    ),
    (r"\bremember|\brecall|\bmemor|\blesson", ("memory", "recall", "lesson")),
    (r"\btask\b|\btodo\b|\bbacklog", ("task",)),
)
_CORE_NAMES = frozenset(
    {
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "glob",
        "list_dir",
        "tool_search",
        "tool_schema",
        "skill_search",
        "skill_invoke",
        "tool_result_get",
        "ask_user",
        "finish",
    }
)
_CORE_NAME_FRAGS = (
    "ask_user",
    "ask_followup",
    "attempt_completion",
    "memory_recall",
    "tool_search",
)


def _is_core(name: str, d) -> bool:
    label = (name or "").lower()
    return (
        bool(getattr(d, "core", False))
        or label in _CORE_NAMES
        or any(fragment in label for fragment in _CORE_NAME_FRAGS)
    )


def _cosine(a: list[float], b: list[float]) -> float:
    length_a = math.sqrt(sum(value * value for value in a))
    length_b = math.sqrt(sum(value * value for value in b))
    if not length_a or not length_b:
        return 0.0
    return sum(left * right for left, right in zip(a, b)) / (length_a * length_b)


def schema_budget_chars(window_tokens: int | None) -> int | None:
    if not window_tokens or window_tokens <= 0:
        return None
    return int(window_tokens * SCHEMA_WINDOW_FRACTION * NOMINAL_CHARS_PER_TOKEN)


def _schema_chars(definition) -> int:
    schema = {
        "type": "function",
        "function": {
            "name": getattr(definition, "name", ""),
            "description": getattr(definition, "description", "") or "",
            "parameters": getattr(definition, "parameters", None)
            or {"type": "object", "properties": {}},
        },
    }
    try:
        return len(json.dumps(schema, default=str))
    except (TypeError, ValueError):
        return len(str(schema))


def _name_words(name: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return {word for word in re.split(r"[^a-z0-9]+", spaced.lower()) if word}


def _names_fragment(name: str, fragment: str) -> bool:
    label = name.lower()
    if fragment in (label, re.split(r"/|__", label)[-1]):
        return True
    words = _name_words(name)
    return fragment in words or f"{fragment}s" in words or f"{fragment}es" in words


@dataclass(slots=True)
class _QueryScores:
    text: str
    semantic: dict[str, float] = field(default_factory=dict)
    words: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.words = set(re.findall(r"\w+", self.text.lower()))

    def score(self, name: str, definition, *, threshold: float | None) -> float:
        content = f"{name} {getattr(definition, 'description', '') or ''}".lower()
        overlap = self.words.intersection(re.findall(r"\w+", content))
        lexical = len(overlap) / len(self.words) if self.words else 0.0
        semantic = self.semantic.get(name, 0.0)
        if threshold is None:
            substring = 0.5 if self.text and self.text.lower() in content else 0.0
            return max(lexical, semantic, substring)
        return max(
            lexical if lexical >= _KEYWORD_GATE else 0.0,
            semantic if semantic >= threshold else 0.0,
        )


@dataclass(slots=True)
class _CatalogPage:
    limit: int
    consumed: int = 0
    blocks: list[str] = field(default_factory=list)
    overflow: list[str] = field(default_factory=list)

    def add(self, provider: str, entries: list[tuple[str, str]]) -> None:
        rows = (
            f"- {name}: {description}" if description else f"- {name}"
            for name, description in sorted(entries)
        )
        block = "\n".join((f"[{provider}]", *rows))
        if len(block) + self.consumed > self.limit:
            self.overflow.append(f"{provider} (+{len(entries)} tools)")
            return
        self.blocks.append(block)
        self.consumed += len(block) + 1

    def render(self) -> str:
        tail = (
            ["[more — use tool_search to find these] " + ", ".join(self.overflow)]
            if self.overflow
            else []
        )
        return "\n".join(self.blocks + tail)


class ToolRetriever:
    def __init__(
        self,
        defs: list,
        *,
        k: int = DEFAULT_K,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ) -> None:
        self._defs = list(defs)
        self._by_name = dict((getattr(item, "name", ""), item) for item in self._defs)
        self._k = max(1, int(k))
        self._threshold = semantic_threshold
        self._core = {
            name for name, item in self._by_name.items() if _is_core(name, item)
        }
        self._sticky: set[str] = set()
        self._carried: set[str] = set()
        self._last_surfaced = len(self._defs)
        self._texts = {
            name: tool_text(name, getattr(item, "description", "") or "")
            for name, item in self._by_name.items()
        }
        self._chars = {name: _schema_chars(item) for name, item in self._by_name.items()}

    def warm(self) -> None:
        """Queue missing tool vectors on the index's background worker."""
        try:
            embedder = bound_embedder()
            if embedder is not None:
                tool_vectors().want(default_path(), embedder, self._texts.values())
        except Exception:
            logger.debug("tool retrieval warm failed", exc_info=True)

    def mark_used(self, tool_name: str) -> None:
        self._sticky.update({tool_name}.intersection(self._by_name))

    def _structural(self, query: str) -> set[str]:
        return {
            name
            for name in self._by_name
            if any(
                _names_fragment(name, fragment)
                for pattern, hints in _STRUCTURAL_HINTS
                if hints and re.search(pattern, query.lower())
                for fragment in hints
            )
        }

    def _semantic(self, text: str, names: set[str]) -> dict[str, float]:
        if not text or not names:
            return {}
        embedder = bound_embedder()
        if embedder is None:
            return {}
        path = default_path()
        index = tool_vectors()
        texts = {name: self._texts[name] for name in names if name in self._texts}
        known = index.vectors(path, embedder.model, texts.values())
        if not known:
            return {}
        try:
            query_vector = embedder.one(text)
        except Exception:
            query_vector = None
        if not query_vector:
            return {}
        return {
            name: _cosine(query_vector, known[tool_text_value])
            for name, tool_text_value in texts.items()
            if tool_text_value in known
        }

    def _query_scores(self, text: str, names: set[str]) -> _QueryScores:
        return _QueryScores(text, self._semantic(text, names))

    def _rank(self, query: _QueryScores, names, *, selection: bool) -> list[tuple[float, str]]:
        ranked = []
        for name in names:
            score = query.score(
                name,
                self._by_name[name],
                threshold=self._threshold if selection else None,
            )
            if score > 0 or (not selection and not query.text):
                ranked.append((score, name))
        return sorted(ranked, key=lambda row: (-row[0], row[1]))

    def _pool(self, restrict: set[str] | None) -> list:
        return [
            definition
            for definition in self._defs
            if restrict is None or getattr(definition, "name", "") in restrict
        ]

    def select(
        self,
        query: str,
        *,
        restrict: set[str] | None = None,
        budget_chars: int | None = None,
    ) -> list:
        try:
            return self._select(query, restrict=restrict, budget_chars=budget_chars)
        except Exception:
            logger.debug(
                "tool retrieval failed — surfacing full catalog", exc_info=True
            )
            return self._pool(restrict)

    def _select(
        self,
        query: str,
        *,
        restrict: set[str] | None = None,
        budget_chars: int | None = None,
    ) -> list:
        pool = self._pool(restrict)
        pool_names = {getattr(item, "name", "") for item in pool}
        total = len(pool)
        within_budget = budget_chars is None or sum(
            self._chars.get(name, 0) for name in pool_names
        ) <= budget_chars
        if total <= self._k and within_budget:
            self._carried = self._structural((query or "").strip())
            return pool
        text = (query or "").strip()
        core = self._core & pool_names
        sticky = (self._sticky & pool_names) - core
        hinted = self._structural(text)
        carried, self._carried = self._carried, hinted
        structural = ((hinted or carried) & pool_names) - core - sticky
        scores = self._query_scores(text, pool_names - core)

        def ranked(names):
            return [name for _, name in self._rank(scores, names, selection=True)]

        selected = set(core)
        used = sum(self._chars.get(name, 0) for name in core)

        def admit(name: str) -> bool:
            nonlocal used
            size = self._chars.get(name, 0)
            if budget_chars is not None and used + size > budget_chars:
                return False
            selected.add(name)
            used += size
            return True

        required = sticky | structural
        for name in sorted(
            required,
            key=lambda item: (
                -scores.score(item, self._by_name[item], threshold=None),
                item,
            ),
        ):
            admit(name)
        room = max(0, self._k - len(selected))
        tried = core | sticky | structural
        for name in ranked(pool_names - tried):
            if room <= 0:
                break
            if admit(name):
                room -= 1

        if len(selected) >= total:
            self._last_surfaced = total
            return pool
        self._last_surfaced = len(selected)
        return [self._by_name[name] for name in self._by_name if name in selected]

    def reduced(self) -> bool:
        return self._k < len(self._defs)

    def hidden_count(self) -> int:
        surfaced = getattr(self, "_last_surfaced", len(self._defs))
        return max(len(self._defs) - surfaced, 0)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        scores = self._query_scores((query or "").strip(), set(self._by_name))
        ranked = self._rank(scores, self._by_name, selection=False)
        return [
            {
                "name": name,
                "description": (getattr(self._by_name[name], "description", "") or "")[
                    :200
                ],
            }
            for _, name in ranked[:limit]
        ]

    async def search_async(
        self,
        query: str,
        limit: int = 20,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[dict]:
        if cancelled is not None and cancelled():
            raise asyncio.CancelledError
        matches = await asyncio.to_thread(self.search, query, limit)
        if cancelled is not None and cancelled():
            raise asyncio.CancelledError
        return matches

    def catalog(self, *, exclude: set[str] | None = None, max_chars: int = 6000) -> str:
        groups: dict[str, list[tuple[str, str]]] = {}
        for name, definition in self._by_name.items():
            if exclude and name in exclude:
                continue
            provider = getattr(definition, "provider", "") or "other"
            description = (
                (getattr(definition, "description", "") or "")
                .strip()
                .partition("\n")[0][:100]
            )
            groups.setdefault(provider, []).append((name, description))
        page = _CatalogPage(max_chars)
        for provider in sorted(groups):
            page.add(provider, groups[provider])
        return page.render()

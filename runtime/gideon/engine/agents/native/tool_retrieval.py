"""Select a turn's schema budget and search the complete tool inventory."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
DEFAULT_K = 48
DEFAULT_SEMANTIC_THRESHOLD = 0.55
_KEYWORD_GATE = 0.5
_STRUCTURAL_HINTS = (
    (r"https?://|www\.|\.com\b|\.org\b", ("web", "fetch", "url", "search", "browse")),
    (
        r"\bschedul|\bremind|\bcron\b|every (day|week|hour)|daily|weekly",
        ("schedule", "cron", "trigger"),
    ),
    (
        r"/|\.py\b|\.ts\b|\.md\b|\bfile\b|\bdirectory\b|\bfolder\b",
        ("read", "write", "edit", "file", "dir", "glob", "grep"),
    ),
    (
        r"\bshell\b|\bbash\b|\bcommand\b|\bterminal\b|\bexecute\b|\brun\b|\$\s"
        r"|\b(npm|pip|make|cargo|go|node|python|pytest|ls|cat|echo|chmod|mkdir|curl)\b"
        r"|\bgit\b|commit|diff|branch|stage|\btest|\bpytest|\bspec\b|assert|lint|build",
        ("bash", "shell", "exec", "run", "command", "terminal"),
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


def _active_embedder():
    try:
        from gideon.extensions.skills.surfacing import _active_embedder as resolve

        return resolve()
    except Exception:
        return None, ""


@dataclass(slots=True)
class _QueryScores:
    text: str
    vector: list[float] | None
    words: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.words = set(re.findall(r"\w+", self.text.lower()))

    def score(self, name: str, definition, vector, *, threshold: float | None) -> float:
        content = f"{name} {getattr(definition, 'description', '') or ''}".lower()
        overlap = self.words.intersection(re.findall(r"\w+", content))
        lexical = len(overlap) / len(self.words) if self.words else 0.0
        semantic = (
            _cosine(self.vector, vector) if self.vector is not None and vector else 0.0
        )
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
        self._embed_model = ""
        self._embed_cache: dict[str, list[float] | None] = {}
        self._last_surfaced = len(self._defs)

    def mark_used(self, tool_name: str) -> None:
        self._sticky.update({tool_name}.intersection(self._by_name))

    def _structural(self, query: str) -> set[str]:
        fragments = {
            fragment
            for pattern, hints in _STRUCTURAL_HINTS
            if re.search(pattern, query.lower())
            for fragment in hints
        }
        return {
            name
            for name in self._by_name
            if any(part in name.lower() for part in fragments)
        }

    def _ensure_embeddings(self, embed_fn, model: str) -> None:
        if self._embed_model != model:
            self._embed_model = model
            self._embed_cache.clear()
        missing = self._by_name.keys() - self._embed_cache.keys()
        for name in self._by_name:
            if name not in missing:
                continue
            description = getattr(self._by_name[name], "description", "") or ""
            try:
                vector = embed_fn(f"{name}: {description}".strip())
            except Exception:
                vector = None
            self._embed_cache[name] = vector

    def _query_scores(self, text: str, *, tolerate_cache_error: bool) -> _QueryScores:
        embed, model = _active_embedder()
        vector = None
        if embed is not None and text:
            try:
                vector = embed(text)
            except Exception:
                pass
            if vector is not None:
                try:
                    self._ensure_embeddings(embed, model)
                except Exception:
                    if not tolerate_cache_error:
                        raise
                    vector = None
        return _QueryScores(text, vector)

    def _rank(
        self, query: _QueryScores, names, *, selection: bool
    ) -> list[tuple[float, str]]:
        ranked = []
        for name in names:
            score = query.score(
                name,
                self._by_name[name],
                self._embed_cache.get(name),
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

    def select(self, query: str, *, restrict: set[str] | None = None) -> list:
        try:
            return self._select(query, restrict=restrict)
        except Exception:
            logger.debug(
                "tool retrieval failed — surfacing full catalog", exc_info=True
            )
            return self._pool(restrict)

    def _select(self, query: str, *, restrict: set[str] | None = None) -> list:
        pool = self._pool(restrict)
        if len(pool) <= self._k:
            return pool
        text = (query or "").strip()
        available = {getattr(item, "name", "") for item in pool}
        chosen = available.intersection(
            self._core | self._sticky | self._structural(text)
        )
        scores = self._query_scores(text, tolerate_cache_error=False)
        candidates = (
            name for name in self._by_name if name in available and name not in chosen
        )
        ranked = self._rank(scores, candidates, selection=True)
        chosen.update(name for _, name in ranked[: max(0, self._k - len(chosen))])
        self._last_surfaced = min(len(chosen), len(pool))
        if len(chosen) >= len(pool):
            return pool
        return [self._by_name[name] for name in self._by_name if name in chosen]

    def reduced(self) -> bool:
        return self._k < len(self._defs)

    def hidden_count(self) -> int:
        surfaced = getattr(self, "_last_surfaced", len(self._defs))
        return max(len(self._defs) - surfaced, 0)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        scores = self._query_scores((query or "").strip(), tolerate_cache_error=True)
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

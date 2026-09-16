"""Semantic duplicate admission and deterministic selection of the richer copy."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

FUZZY_COSINE_MIN = 0.90
FILENAME_SIM_MIN = 0.85
_DATE_PATTERNS = [
    re.compile(r"(?<![0-9])(\d{4}-\d{2}-\d{2})(?![0-9])"),
    re.compile(r"(?<![0-9])(\d{4}[-_/]?[qQ][1-4])(?![0-9])"),
    re.compile(r"(?<![0-9])(\d{4}[-_/]\d{2})(?![0-9])"),
    re.compile(r"(?<![0-9])(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})(?![0-9])"),
    re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b", re.I
    ),
    re.compile(r"\bweek\s*\d{1,2}\b", re.I),
]
_STOP_STEM = re.compile(r"[^a-z0-9]+")


@dataclass
class DupVerdict:
    is_dup: bool
    reason: str
    winner_id: str | None = None
    loser_id: str | None = None
    cosine: float = 0.0
    filename_sim: float = 0.0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    squared = [sum(component * component for component in vector) for vector in (a, b)]
    lengths = [math.sqrt(value) for value in squared]
    if 0.0 in lengths:
        return 0.0
    products = (left * right for left, right in zip(a, b))
    return sum(products) / (lengths[0] * lengths[1])


def normalize_filename_stem(name: str) -> str:
    if not name:
        return ""
    filename = name.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0].lower() if "." in filename else filename.lower()
    for pattern in _DATE_PATTERNS:
        stem = pattern.sub(" ", stem)
    return " ".join(filter(None, _STOP_STEM.split(stem)))


def extract_series_date(text: str) -> str | None:
    if text:
        matches = (pattern.search(text) for pattern in _DATE_PATTERNS)
        matched = next((value for value in matches if value is not None), None)
        if matched:
            return matched.group(0).lower()
    return None


def _stem_similarity(a: str, b: str) -> float:
    left, right = set(a.split()), set(b.split())
    return (
        len(left.intersection(right)) / len(left.union(right))
        if left and right
        else 0.0
    )


def filename_similarity(name_a: str, name_b: str) -> float:
    stems = tuple(map(normalize_filename_stem, (name_a, name_b)))
    return _stem_similarity(*stems)


@dataclass(frozen=True)
class _Copy:
    row: dict

    @property
    def name(self):
        return self.row.get("title") or self.row.get("file_path") or ""

    @property
    def series(self):
        return extract_series_date(self.name) or extract_series_date(
            self.row.get("summary", "")
        )

    def rank(self) -> tuple:
        content = self.row.get("content_len")
        size = None
        if content is not None:
            try:
                size = int(content or 0)
            except (TypeError, ValueError):
                pass
        if size is None:
            size = int(self.row.get("word_count", 0) or 0)
        complete = int(str(self.row.get("processing_status", "")).lower() == "done")
        document = int(
            str(self.row.get("item_type", "")).lower()
            not in ("bookmark", "url", "link")
        )
        return complete, document, size, str(self.row.get("created_at", "") or "")


def format_recall_winner(a: dict, b: dict) -> tuple[dict, dict]:
    if _Copy(b).rank() > _Copy(a).rank():
        return b, a
    return a, b


@dataclass(frozen=True)
class _Comparison:
    candidate: _Copy
    existing: _Copy
    filename_score: float
    vector_score: float

    def decide(self, filename_floor: float, vector_floor: float) -> DupVerdict:
        scores = dict(cosine=self.vector_score, filename_sim=self.filename_score)
        if self.filename_score < filename_floor:
            return DupVerdict(False, "filename too different", **scores)
        if self.vector_score < vector_floor:
            return DupVerdict(False, "cosine below threshold", **scores)
        first, second = self.candidate.series, self.existing.series
        if first and second and first != second:
            return DupVerdict(False, "series date differs — distinct", **scores)
        winner, loser = format_recall_winner(self.candidate.row, self.existing.row)
        return DupVerdict(
            True,
            "fuzzy dup (filename+cosine+date-gate)",
            winner_id=winner.get("id"),
            loser_id=loser.get("id"),
            **scores,
        )


def resolve_duplicate(
    candidate: dict,
    existing: dict,
    *,
    cosine_min: float = FUZZY_COSINE_MIN,
    filename_sim_min: float = FILENAME_SIM_MIN,
) -> DupVerdict:
    copies = (_Copy(candidate), _Copy(existing))
    title_score = filename_similarity(copies[0].name, copies[1].name)
    vector_score = cosine_similarity(
        candidate.get("embedding") or [], existing.get("embedding") or []
    )
    return _Comparison(*copies, title_score, vector_score).decide(
        filename_sim_min, cosine_min
    )

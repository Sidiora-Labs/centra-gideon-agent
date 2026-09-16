"""Deterministic source caching, document cuts and bibliography identities."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gideon.cognition.knowledge.readers import PdfLine, PdfStructure, read_pdf_structure

HEADING_SIZE_RATIO = 1.1

HEADING_MAX_CHARS = 120

KEEP_FIRST_PAGES = 3

KEEP_LAST_PAGES = 2

META_PAGES = 2

BRIEF_MIN_FRACTION = 0.10

BRIEF_MAX_FRACTION = 0.25

TITLE_MATCH_RATIO = 0.85

TITLE_WINDOW_STEP_CHARS = 4

TITLE_MIN_WORDS = 4

AUTHOR_YEAR_PROXIMITY_CHARS = 120

MIN_REFERENCE_CHARS = 20

MAX_REFERENCES = 500

SOURCE_ARXIV = "arxiv"

SOURCE_DOI = "doi"

SOURCE_PDF = "pdf"

SOURCE_URL = "url"

ROLE_ABSTRACT = "abstract"

ROLE_INTRODUCTION = "introduction"

ROLE_METHOD = "method"

ROLE_RESULTS = "results"

ROLE_DISCUSSION = "discussion"

ROLE_CONCLUSION = "conclusion"

ROLE_REFERENCES = "references"

ROLE_OTHER = "other"

STRATEGY_OUTLINE = "outline"

STRATEGY_FONT = "font"

STRATEGY_HEADER = "header"

_STRATEGY_RANK = {STRATEGY_OUTLINE: 0, STRATEGY_FONT: 1, STRATEGY_HEADER: 2}

SLICE_BRIEF = "brief"

SLICE_BODY = "body"

SLICE_META = "meta"

SLICE_FULL = "full"

PERSISTED_SLICES = (SLICE_BRIEF, SLICE_BODY, SLICE_META)

BRIEF_ROLES = (ROLE_ABSTRACT, ROLE_INTRODUCTION, ROLE_CONCLUSION)

BODY_ROLES = (ROLE_METHOD, ROLE_RESULTS, ROLE_DISCUSSION)

TIER_ARXIV = "arxiv"

TIER_DOI = "doi"

TIER_TITLE = "title"

TIER_AUTHOR_YEAR = "author_year"

SLICE_NODE_PREFIX = "slice:"

_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")

_DOI = re.compile(r"\b(10\.\d{4,}(?:\.\d+)*/[^\s\"'<>,;]+)")

_ARXIV_MARKER = re.compile(r"arxiv", re.IGNORECASE)

_BARE_ARXIV_ID = re.compile(r"^\s*(\d{4}\.\d{4,5})(?:v\d+)?\s*$")

_PDF_MAGIC = b"%PDF-"

_HEX64_RE = re.compile(r"[0-9a-f]{64}")

_CACHED_SUFFIXES = frozenset({".pdf", ".bin"})

_ROLE_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (ROLE_ABSTRACT, re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?abstract\b", re.IGNORECASE)),
    (
        ROLE_REFERENCES,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:references|bibliography|works\s+cited)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_INTRODUCTION,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:introduction|background|motivation)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_METHOD,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:methods?|methodology|approach|architecture|"
            r"implementation|experimental\s+setup|our\s+model)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_RESULTS,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:results|evaluation|experiments?|findings|analysis)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_DISCUSSION,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:discussion|limitations|related\s+work|future\s+work|"
            r"threats\s+to\s+validity)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_CONCLUSION,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:conclusions?|concluding\s+remarks|summary)\b",
            re.IGNORECASE,
        ),
    ),
)

_ROLES_FOR_SLICE: dict[str, tuple[str, ...]] = {
    SLICE_BRIEF: BRIEF_ROLES,
    SLICE_BODY: BODY_ROLES,
    SLICE_META: (),
}

_ENTRY_BRACKET = re.compile(r"\[\d{1,3}\]")

_ENTRY_NUMBERED = re.compile(r"(?m)^\s*\d{1,3}\.\s+")

_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

_SURNAME = re.compile(r"\b([A-Z][a-z]{1,})\b")

_TITLE_SPLIT = re.compile(r"[.;]\s+|[\"“”]")


@dataclass(frozen=True)
class SourceRef:
    kind: str
    url: str
    identifier: str = ""
    raw: str = ""


class SourceFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedSource:
    path: Path
    sha256: str
    from_cache: bool
    ref: SourceRef


@dataclass(frozen=True)
class Section:
    title: str
    role: str
    start: int
    end: int
    page: int
    strategy: str


@dataclass(frozen=True)
class DocumentText:
    text: str
    page_spans: tuple[tuple[int, int], ...]

    @property
    def page_count(self) -> int:
        return len(self.page_spans)


@dataclass(frozen=True)
class Slice:
    role: str
    text: str
    fraction: float
    section_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reference:
    key: str
    tier: str
    raw: str
    title: str = ""
    year: str = ""


@dataclass(frozen=True)
class SliceResult:
    sections: tuple[Section, ...] = ()
    slices: tuple[Slice, ...] = ()
    references: tuple[Reference, ...] = ()
    full_text: str = ""
    page_count: int = 0
    bibliography_start: int = 0
    body_font_size: float = 0.0
    unkeyed_references: int = 0
    strategies: tuple[str, ...] = ()

    def slice_for(self, role: str) -> Slice | None:
        return (
            Slice(SLICE_FULL, self.full_text, 1.0)
            if role == SLICE_FULL
            else next((piece for piece in self.slices if piece.role == role), None)
        )


def slice_node_type(role: str) -> str:
    return "".join((SLICE_NODE_PREFIX, role))


def is_slice_row(node_type: str) -> bool:
    return str(node_type or "").startswith(SLICE_NODE_PREFIX)


def _arxiv_pdf_url(identifier: str) -> str:
    return "https://arxiv.org/pdf/" + identifier


def _safe_parse(text: str):
    try:
        parsed = urlparse(text)
    except ValueError:
        parsed = urlparse("")
    return parsed


def sniff_source(raw: str) -> SourceRef | None:
    text = (raw or "").strip()
    if not text:
        return None
    bare = _BARE_ARXIV_ID.match(text)
    parsed = _safe_parse(text)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    arxiv = bare
    if arxiv is None and (_ARXIV_MARKER.search(text) or host.endswith("arxiv.org")):
        arxiv = _ARXIV_ID.search(text)
    if arxiv is not None:
        identifier = arxiv.group(1)
        return SourceRef(SOURCE_ARXIV, _arxiv_pdf_url(identifier), identifier, text)
    doi = _DOI.search(text)
    if doi is not None:
        identifier = doi.group(1).rstrip(".")
        return SourceRef(SOURCE_DOI, "https://doi.org/" + identifier, identifier, text)
    if host and parsed.scheme.lower() in {"http", "https"}:
        kind = (
            SOURCE_PDF if (parsed.path or "").lower().endswith(".pdf") else SOURCE_URL
        )
        return SourceRef(kind, text, raw=text)
    return None


def is_pdf_bytes(body: bytes) -> bool:
    return bytes(body or b"").startswith(_PDF_MAGIC)


def source_cache_dir() -> Path:
    from gideon.cognition.knowledge import knowledge_files_dir

    root = Path(knowledge_files_dir()).joinpath("sources")
    root.mkdir(exist_ok=True, parents=True)
    return root


def _digest(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def _pointer_path(ref: SourceRef) -> Path:
    name = "ref-" + _digest(ref.url.encode("utf-8")) + ".json"
    return source_cache_dir().joinpath(name)


def _original_path(sha256: str, suffix: str) -> Path:
    name = "sha256-" + sha256 + suffix
    return source_cache_dir().joinpath(name)


class _SourceArchive:
    def __init__(self, ref):
        self.ref = ref

    def lookup(self):
        try:
            record = json.loads(_pointer_path(self.ref).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        digest, suffix = (str(record.get(key) or "") for key in ("sha256", "suffix"))
        if _HEX64_RE.fullmatch(digest) is None or suffix not in _CACHED_SUFFIXES:
            return None
        original = _original_path(digest, suffix)
        return (
            FetchedSource(original, digest, True, self.ref)
            if original.is_file()
            else None
        )

    def save(self, payload):
        body = bytes(payload or b"")
        if not body:
            raise SourceFetchError(f"empty response for {self.ref.url}")
        digest = _digest(body)
        suffix = ".pdf" if is_pdf_bytes(body) else ".bin"
        path = _original_path(digest, suffix)
        if not path.is_file():
            path.write_bytes(body)
        record = dict(
            sha256=digest, suffix=suffix, url=self.ref.url, kind=self.ref.kind
        )
        _pointer_path(self.ref).write_text(json.dumps(record), encoding="utf-8")
        return FetchedSource(path, digest, False, self.ref)


def cached_source(ref: SourceRef) -> FetchedSource | None:
    return _SourceArchive(ref).lookup()


async def fetch_source(
    ref: SourceRef, *, fetch_fn: Callable[[str], Any] | None = None
) -> FetchedSource:
    result = cached_source(ref)
    if result is None:
        fetcher = fetch_fn or _default_fetch
        result = _SourceArchive(ref).save(await fetcher(ref.url))
    return result


async def _default_fetch(url: str) -> bytes:
    from gideon.security.net.client import fetch as net_fetch
    from gideon.security.net.policy import SOURCE, egress_policy_for

    policy = egress_policy_for(SOURCE)
    result = await net_fetch(url, policy=policy)
    status = int(getattr(result, "status", 0))
    if status >= 400:
        raise SourceFetchError(f"HTTP {result.status} for {url}")
    payload = getattr(result, "body", b"")
    return bytes(payload or b"")


def role_for(title: str) -> str:
    normalized = " ".join(str(title or "").split())
    return next((role for role, cue in _ROLE_CUES if cue.match(normalized)), ROLE_OTHER)


def structure_from_text(text: str) -> PdfStructure:
    body = str(text or "")
    lines = tuple(
        PdfLine(0, clean, 0.0, len(clean))
        for line in body.splitlines()
        if (clean := line.strip())
    )
    return PdfStructure((body,), lines, ())


def flatten(structure: PdfStructure) -> DocumentText:
    pages = [str(page or "") for page in structure.pages]
    spans = []
    for page in pages:
        start = spans[-1][1] + 1 if spans else 0
        spans.append((start, start + len(page)))
    return DocumentText("\n".join(pages), tuple(spans))


def body_font_size(lines: tuple[PdfLine, ...]) -> float:
    weights = {}
    for line in lines:
        if line.size > 0:
            weights.setdefault(line.size, 0)
            weights[line.size] += max(1, line.char_count)
    return min(weights, key=lambda size: (-weights[size], size), default=0.0)


class _HeadingLocator:
    def __init__(self, document, strategy):
        self.document, self.strategy = document, strategy
        self.cursor = 0
        self.found = []

    def locate(self, title, *, page=None, retry=False):
        offset = self.document.text.find(title, self.cursor)
        if offset < 0 and retry:
            page_start = (
                self.document.page_spans[page][0]
                if page < self.document.page_count
                else 0
            )
            offset = self.document.text.find(title, page_start)
        if offset < 0:
            return
        self.found.append(
            (
                offset,
                title,
                self.strategy,
                _page_of(self.document, offset) if page is None else page,
            )
        )
        self.cursor = offset + len(title)


def _outline_candidates(
    structure: PdfStructure, document: DocumentText
) -> list[tuple[int, str, str, int]]:
    scan = _HeadingLocator(document, STRATEGY_OUTLINE)
    for title in structure.outline:
        if role_for(title) != ROLE_OTHER:
            scan.locate(title)
    return scan.found


def _font_candidates(
    structure: PdfStructure, document: DocumentText
) -> list[tuple[int, str, str, int]]:
    body = body_font_size(structure.lines)
    scan = _HeadingLocator(document, STRATEGY_FONT)
    if body > 0:
        threshold = body * HEADING_SIZE_RATIO
        for line in structure.lines:
            if line.size > threshold and len(line.text) <= HEADING_MAX_CHARS:
                scan.locate(line.text, page=line.page, retry=True)
    return scan.found


def _header_candidates(
    structure: PdfStructure, document: DocumentText
) -> list[tuple[int, str, str, int]]:
    scan = _HeadingLocator(document, STRATEGY_HEADER)
    for line in structure.lines:
        if len(line.text) <= HEADING_MAX_CHARS and role_for(line.text) != ROLE_OTHER:
            scan.locate(line.text, page=line.page)
    return scan.found


def _page_of(document: DocumentText, offset: int) -> int:
    return next(
        (
            index
            for index, (start, end) in enumerate(document.page_spans)
            if start <= offset <= end
        ),
        max(0, document.page_count - 1),
    )


def detect_sections(structure: PdfStructure) -> tuple[Section, ...]:
    document = flatten(structure)
    proposals = _outline_candidates(structure, document) + _font_candidates(
        structure, document
    )
    if not proposals:
        proposals = _header_candidates(structure, document)
    by_position = {}
    for hit in sorted(
        proposals, key=lambda hit: (hit[0], _STRATEGY_RANK.get(hit[2], 99), hit[1])
    ):
        by_position.setdefault(hit[0], hit)
    headings = list(by_position.values())
    limits = [hit[0] for hit in headings[1:]] + [len(document.text)]
    return tuple(
        Section(title, role_for(title), start, end, page, strategy)
        for (start, title, strategy, page), end in zip(headings, limits)
    )


def _bibliography_start(sections: tuple[Section, ...], document: DocumentText) -> int:
    return next(
        (section.start for section in sections if section.role == ROLE_REFERENCES),
        len(document.text),
    )


def _merge_ranges(ranges: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    selected = sorted((start, end) for start, end in ranges if start < end)
    if not selected:
        return ()
    output = [selected[0]]
    for start, end in selected[1:]:
        left, right = output[-1]
        if start > right:
            output.append((start, end))
        elif end > right:
            output[-1] = left, end
    return tuple(output)


def _page_ranges(
    document: DocumentText, indices: Iterable[int], limit: int
) -> list[tuple[int, int]]:
    spans = []
    for index in indices:
        if 0 <= index < document.page_count:
            start, end = document.page_spans[index]
            if start < min(end, limit):
                spans.append((start, min(end, limit)))
    return spans


def _kept_page_ranges(
    document: DocumentText, bibliography_start: int
) -> tuple[tuple[int, int], ...]:
    eligible = [
        index
        for index, span in enumerate(document.page_spans)
        if span[0] < bibliography_start
    ]
    front = eligible[: max(0, KEEP_FIRST_PAGES)]
    back = eligible[-KEEP_LAST_PAGES:] if KEEP_LAST_PAGES > 0 else []
    return _merge_ranges(
        _page_ranges(document, sorted(set(front).union(back)), bibliography_start)
    )


def _text_for(document: DocumentText, ranges: Iterable[tuple[int, int]]) -> str:
    pieces = map(lambda span: document.text[span[0] : span[1]].strip(), ranges)
    return "\n\n".join(filter(None, pieces)).strip()


def _truncate_on_whitespace(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    prefix = text[:limit]
    boundary = prefix.rfind(" ")
    return (prefix[:boundary] if boundary > 0 else prefix).rstrip()


def _role_ranges(sections, roles, limit):
    return [
        (section.start, min(section.end, limit))
        for section in sections
        if section.role in roles
    ]


def _clamped_brief(
    document: DocumentText, sections: tuple[Section, ...], bibliography_start: int
) -> str:
    spans = _merge_ranges(_role_ranges(sections, BRIEF_ROLES, bibliography_start))
    text = _text_for(document, spans)
    budget = max(0, bibliography_start)
    if budget:
        minimum, maximum = math.ceil(budget * BRIEF_MIN_FRACTION), int(
            budget * BRIEF_MAX_FRACTION
        )
        if len(text) < minimum:
            text = _text_for(document, _merge_ranges([*spans, (0, minimum)]))
        if len(text) > maximum:
            text = _truncate_on_whitespace(text, maximum)
    return text


class _DocumentCuts:
    def __init__(self, structure):
        self.structure = structure
        self.document = flatten(structure)
        self.sections = detect_sections(structure)
        self.bibliography = _bibliography_start(self.sections, self.document)

    def pieces(self):
        document, limit = self.document, self.bibliography
        body_ranges = _role_ranges(self.sections, BODY_ROLES, limit)
        body_ranges.extend(_kept_page_ranges(document, limit))
        content = (
            (SLICE_BRIEF, _clamped_brief(document, self.sections, limit)),
            (SLICE_BODY, _text_for(document, _merge_ranges(body_ranges))),
            (
                SLICE_META,
                _text_for(document, _page_ranges(document, range(META_PAGES), limit)),
            ),
        )
        return tuple(
            Slice(
                role,
                text,
                round(len(text) / max(0, limit), 4) if limit > 0 else 0.0,
                tuple(
                    section.title
                    for section in self.sections
                    if section.role in _ROLES_FOR_SLICE.get(role, ())
                ),
            )
            for role, text in content
            if text.strip()
        )

    def result(self):
        named = any(section.role != ROLE_OTHER for section in self.sections)
        pieces = self.pieces() if named else ()
        references, unkeyed = (
            extract_references(self.document.text, self.bibliography)
            if named
            else ((), 0)
        )
        return SliceResult(
            sections=self.sections,
            slices=pieces,
            references=references,
            full_text=self.document.text,
            page_count=self.document.page_count,
            bibliography_start=self.bibliography,
            body_font_size=body_font_size(self.structure.lines),
            unkeyed_references=unkeyed,
            strategies=tuple(sorted({s.strategy for s in self.sections})),
        )


def slice_document(*, file_path: str = "", text: str = "") -> SliceResult:
    structure = (
        read_pdf_structure(file_path)
        if file_path and Path(file_path).suffix.lower() == ".pdf"
        else None
    )
    readable = structure is not None and any(
        (page or "").strip() for page in structure.pages
    )
    return slice_structure(structure if readable else structure_from_text(text))


def slice_structure(structure: PdfStructure) -> SliceResult:
    return _DocumentCuts(structure).result()


def _split_entries(body: str) -> list[str]:
    heading = r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:references|bibliography|works\s+cited)\b[^\n]*\n?"
    text = re.sub(heading, "", body, count=1, flags=re.IGNORECASE)
    positions = [match.start() for match in _ENTRY_BRACKET.finditer(text)]
    if not positions:
        positions = [match.start() for match in _ENTRY_NUMBERED.finditer(text)]
    pieces = (
        [text[start:end] for start, end in zip(positions, positions[1:] + [len(text)])]
        if positions
        else re.split(r"\n\s*\n", text)
    )
    normalized = (" ".join(piece.split()) for piece in pieces)
    return [piece for piece in normalized if len(piece) >= MIN_REFERENCE_CHARS]


def _title_candidate(entry: str) -> str:
    candidates = (
        " ".join(str(piece or "").split()).strip("\"'“”.,;: ")
        for piece in _TITLE_SPLIT.split(entry)
    )
    return max(
        (title for title in candidates if len(title.split()) >= TITLE_MIN_WORDS),
        key=len,
        default="",
    )


def _normalize_title(title: str) -> str:
    normalized = str(title or "").lower()
    return re.sub(r"[^a-z0-9 ]+", "", normalized).strip()


def _sliding_ratio(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    width = len(needle)
    windows = (
        (haystack,)
        if width >= len(haystack)
        else (
            haystack[start : start + width]
            for start in range(0, len(haystack) - width + 1, TITLE_WINDOW_STEP_CHARS)
        )
    )
    return max(
        (SequenceMatcher(None, needle, window).ratio() for window in windows),
        default=0.0,
    )


def _fuzzy_match(title: str, existing: list[Reference]) -> str | None:
    needle = _normalize_title(title)
    if not needle:
        return None
    for reference in existing:
        candidate = _normalize_title(reference.title)
        if candidate and _sliding_ratio(needle, candidate) >= TITLE_MATCH_RATIO:
            return reference.key
    return None


class _CitationKey:
    def __init__(self, entry, existing):
        self.entry, self.existing = entry, existing
        year = _YEAR.search(entry)
        self.year = year.group(1) if year else ""

    def identified(self, key, tier):
        return Reference(key, tier, self.entry, _title_candidate(self.entry), self.year)

    def resolve(self):
        arxiv = (
            _ARXIV_ID.search(self.entry) if _ARXIV_MARKER.search(self.entry) else None
        )
        if arxiv:
            return self.identified("arXiv:" + arxiv.group(1), TIER_ARXIV)
        doi = _DOI.search(self.entry)
        if doi:
            return self.identified("doi:" + doi.group(1).rstrip("."), TIER_DOI)
        title = _title_candidate(self.entry)
        if title:
            key = _fuzzy_match(title, self.existing) or "title:" + _normalize_title(
                title
            )
            return Reference(key, TIER_TITLE, self.entry, title, self.year)
        surname = _SURNAME.search(self.entry)
        if (
            surname
            and self.year
            and self.entry.find(self.year) <= AUTHOR_YEAR_PROXIMITY_CHARS
        ):
            key = "author_year:" + surname.group(1).lower() + ":" + self.year
            return Reference(key, TIER_AUTHOR_YEAR, self.entry, year=self.year)
        return None


def _key_entry(entry: str, existing: list[Reference]) -> Reference | None:
    return _CitationKey(entry, existing).resolve()


def extract_references(
    full_text: str, bibliography_start: int
) -> tuple[tuple[Reference, ...], int]:
    text = str(full_text or "")
    if bibliography_start >= len(text):
        return (), 0
    accepted, known, unkeyed = [], set(), 0
    for entry in _split_entries(text[bibliography_start:])[:MAX_REFERENCES]:
        reference = _key_entry(entry, accepted)
        if reference is None:
            unkeyed += 1
        elif reference.key not in known:
            accepted.append(reference)
            known.add(reference.key)
    return tuple(accepted), unkeyed


def slice_rows(result: SliceResult) -> list[dict[str, Any]]:
    output = []
    stripped = result.bibliography_start < len(result.full_text)
    for role in PERSISTED_SLICES:
        piece = result.slice_for(role)
        if piece is not None:
            metadata = dict(
                role=role,
                chars=len(piece.text),
                fraction_of_document=piece.fraction,
                sections=list(piece.section_titles),
                references_stripped=stripped,
            )
            output.append(
                dict(
                    node_type=slice_node_type(role), text=piece.text, metadata=metadata
                )
            )
    return output


def reference_metadata(result: SliceResult) -> dict[str, Any]:
    section_fields = ("title", "role", "page", "strategy")
    reference_fields = ("key", "tier", "title", "year")
    return dict(
        sections=[
            {field: getattr(section, field) for field in section_fields}
            for section in result.sections
        ],
        section_strategies=list(result.strategies),
        references=[
            {field: getattr(ref, field) for field in reference_fields}
            for ref in result.references
        ],
        references_unkeyed=result.unkeyed_references,
    )

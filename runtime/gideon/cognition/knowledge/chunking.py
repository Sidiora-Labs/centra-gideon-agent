"""Section-aware text windows with source line provenance."""

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass

MAX_CHARS = 1500
OVERLAP = 200
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")


@dataclass
class Chunk:
    text: str
    section: str | None
    line_start: int
    line_end: int
    chunk_index: int = 0
    embedding: bytes | None = None
    embedding_provider: str = ""
    embedding_model: str = ""
    section_key: str = ""
    section_digest: str = ""


@dataclass(frozen=True)
class Boundary:
    offset: int
    line: int
    title: str
    level: int


def section_boundaries(content: str) -> list[Boundary]:
    if not content:
        return []
    found = []
    position = 0
    for number, value in enumerate(content.split("\n"), start=1):
        if _HEADING.match(value):
            marker = value.lstrip()
            heading = marker.lstrip("#")
            found.append(
                Boundary(position, number, heading.strip(), len(marker) - len(heading))
            )
        position += len(value) + 1
    return found


class _LineWindow:
    def __init__(self, section: str | None, capacity: int, overlap: int):
        self.section = section
        self.capacity = capacity
        self.overlap = overlap
        self.pending: list[tuple[int, str]] = []
        self.output: list[Chunk] = []

    def flush(self, *, retain: bool = False) -> None:
        if self.pending:
            self.output.append(_mk(self.pending, self.section))
            self.pending = _tail_overlap(self.pending, self.overlap) if retain else []

    def accept(self, entry: tuple[int, str]) -> None:
        number, text = entry
        if len(text) > self.capacity:
            self.flush()
            self.output.extend(
                Chunk(part, self.section, number, number)
                for part in _char_windows(text, self.capacity, self.overlap)
            )
            return
        occupied = sum(len(value) + 1 for _, value in self.pending)
        if self.pending and occupied + len(text) > self.capacity:
            self.flush(retain=True)
        self.pending.append(entry)


def canonical_text(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def content_digest(content: str) -> str:
    return hashlib.sha256(canonical_text(content).encode("utf-8")).hexdigest()


def chunk_text(
    content: str, *, max_chars: int = MAX_CHARS, overlap: int = OVERLAP
) -> list[Chunk]:
    if not content or not content.strip():
        return []
    chunks = []
    occurrences: dict[str | None, int] = defaultdict(int)
    for label, lines in _split_into_sections(canonical_text(content).split("\n")):
        key = json.dumps([label, occurrences[label]], ensure_ascii=False)
        occurrences[label] += 1
        digest = content_digest("\n".join(text for _, text in lines).strip("\n"))
        for part in _size_split(lines, label, max_chars, overlap):
            if part.text.strip():
                part.section_key = key
                part.section_digest = digest
                chunks.append(part)
    for index, part in enumerate(chunks):
        part.chunk_index = index
    return chunks


def _split_into_sections(
    lines: list[str],
) -> list[tuple[str | None, list[tuple[int, str]]]]:
    if not lines:
        return []
    boundaries = {i for i, line in enumerate(lines) if _HEADING.match(line)}
    starts = sorted({0, *boundaries})
    ends = starts[1:] + [len(lines)]
    sections = []
    for start, end in zip(starts, ends):
        label = (
            lines[start].strip().lstrip("#").strip() if start in boundaries else None
        )
        sections.append((label, list(enumerate(lines[start:end], start=start + 1))))
    return sections


def _size_split(numbered, label, max_chars: int, overlap: int) -> list[Chunk]:
    window = _LineWindow(label, max_chars, overlap)
    for entry in numbered:
        window.accept(entry)
    window.flush()
    return window.output


def _mk(lines, label) -> Chunk:
    occupied = [i for i, (_, text) in enumerate(lines) if text.strip()]
    selected = lines[occupied[0] : occupied[-1] + 1] if occupied else lines
    body = "\n".join(text for _, text in selected) if occupied else ""
    return Chunk(body, label, selected[0][0], selected[-1][0])


def _tail_overlap(lines, overlap: int):
    if overlap <= 0:
        return []
    start, used = len(lines), 0
    while start:
        next_size = len(lines[start - 1][1]) + (1 if start < len(lines) else 0)
        if used + next_size > overlap:
            break
        used += next_size
        start -= 1
    return lines[start:]


def _char_windows(text: str, size: int, overlap: int) -> list[str]:
    positions = range(0, len(text), max(1, size - overlap))
    result = []
    for position in positions:
        result.append(text[position : position + size])
        if position + size >= len(text):
            break
    return result

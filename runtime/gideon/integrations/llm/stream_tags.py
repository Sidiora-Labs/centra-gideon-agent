"""Incrementally classify text while withholding incomplete delimiters."""

from __future__ import annotations

from dataclasses import dataclass

KIND_OUTSIDE = "text"


@dataclass
class Segment:
    kind: str
    text: str


class StreamingTagSplitter:
    def __init__(self, tags: dict[str, str]) -> None:
        self._tags = {key.lower(): value for key, value in tags.items()}
        self._openers = {f"<{key}>": key for key in self._tags}
        self._closers = {key: f"</{key}>" for key in self._tags}
        self._buf = ""
        self._open: str | None = None

    @staticmethod
    def _append(output: list[Segment], kind: str, text: str) -> None:
        if not text:
            return
        if output and output[-1].kind == kind:
            output[-1].text += text
        else:
            output.append(Segment(kind, text))

    def _drain_candidate(self, output: list[Segment]) -> None:
        while self._buf:
            candidates = (
                self._openers
                if self._open is None
                else {self._closers[self._open]: None}
            )
            folded = self._buf.lower()
            if folded in candidates:
                self._open = candidates[folded]
                self._buf = ""
                return
            if any(delimiter.startswith(folded) for delimiter in candidates):
                return
            kind = KIND_OUTSIDE if self._open is None else self._tags[self._open]
            self._append(output, kind, self._buf[0])
            self._buf = self._buf[1:]

    def feed(self, chunk: str) -> list[Segment]:
        output: list[Segment] = []
        for character in chunk:
            self._buf += character
            self._drain_candidate(output)
        return output

    def flush(self) -> list[Segment]:
        pending = self._buf
        self._buf, self._open = "", None
        return [Segment(KIND_OUTSIDE, pending)] if pending else []


def make_think_splitter() -> StreamingTagSplitter:
    return StreamingTagSplitter(tags={"think": "thinking"})

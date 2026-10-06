"""Token-window decisions and speech-only text projection for duplex voice."""

import json
import re
from pathlib import Path
from collections import deque
from collections.abc import Iterator

TAIL_WINDOW_WORDS = 6


ECHO_MIN_RUN = 3

_PHRASE_TABLE = Path(__file__).with_name("phrases.json")


def _shipped_phrases(table: object, key: str) -> tuple[str, ...]:
    phrases = table.get(key) if isinstance(table, dict) else None
    if not (
        isinstance(phrases, list)
        and phrases
        and all(isinstance(p, str) and p.strip() for p in phrases)
    ):
        raise RuntimeError(f"{_PHRASE_TABLE} has no {key} phrases")
    return tuple(phrases)


_SHIPPED_TABLE = json.loads(_PHRASE_TABLE.read_text(encoding="utf-8"))
DEFAULT_CONFIRMATION_PHRASES: tuple[str, ...] = _shipped_phrases(_SHIPPED_TABLE, "confirmation")
DEFAULT_EXIT_PHRASES: tuple[str, ...] = _shipped_phrases(_SHIPPED_TABLE, "exit")


VOICE_DISCLAIMER = "(Transcribed from voice; transcription may be inaccurate.)"


DEFAULT_PUSH_TO_TALK_CHORD = "CommandOrControl+Shift+Space"

_WORD_RE = re.compile(r"[A-Za-z0-9'\u2018\u2019\u02bc]+")
_APOSTROPHE_RE = re.compile(r"['\u2018\u2019\u02bc]")

_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_OPEN_FENCE_RE = re.compile(r"(?:```|~~~).*\Z", re.DOTALL)
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE_RE = re.compile(r"`+([^`]*)`+")
_URL_RE = re.compile(
    r"\b(?:https?|ftp)://([^\s/?#]+)\S*|\bwww\.([^\s/?#]+)\S*", re.IGNORECASE
)
_FLAG_RE = re.compile(r"(?<!\S)--?[A-Za-z][\w-]*(?:=\S*)?")
_PATH_RE = re.compile(r"(?<!\S)~?[\w.@+-]*(?:/[\w.@+-]+)+/?")
_EMPHASIS_RE = re.compile(r"(\*\*|__|~~|\*|_)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s{0,3}>+\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_RULE_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
_REPEATED_PUNCT_RE = re.compile(r"([,.;:!?])(?:\s*\1)+")


_CODE_BLOCK_SPOKEN = " code block. "


def _words(text: str) -> list[str]:
    """Word tokens, lowercased with apostrophes removed; punctuation and markup discarded."""

    folded = (_APOSTROPHE_RE.sub("", m.group(0)).lower() for m in _WORD_RE.finditer(text))
    return [w for w in folded if w]


def _runs(tokens: list[str], width: int) -> Iterator[tuple[str, ...]]:
    window: deque[str] = deque(maxlen=width)
    for token in tokens:
        window.append(token)
        if len(window) == width:
            yield tuple(window)


def _spells_phrase_in_tail(
    tokens: list[str], phrase: list[str], tail_words: int, *, split_words: bool
) -> bool:
    """True when a run of whole ``tokens`` inside the trailing window spells ``phrase``.

    A run spells a phrase when the two are equal with the spaces between their words
    taken out: speech-to-text writes "never mind" as "Nevermind", "never-mind" or
    "Never mind.", and each of them is the phrase. Only whole words count, so "remind"
    or "whenever" never supplies part of one. With ``split_words`` a single word of the
    phrase may also arrive as two ("never mind" for a "nevermind" phrase); without it a
    run may only join the phrase's own words together.
    """

    key = "".join(phrase)
    if not key:
        return False
    # Where the phrase itself has a boundary between two of its words, as offsets into key.
    bounds: set[int] = set()
    offset = 0
    for part in phrase[:-1]:
        offset += len(part)
        bounds.add(offset)
    for i in range(len(tokens)):
        run = ""
        for j in range(i, len(tokens)):
            run += tokens[j]
            if not key.startswith(run):
                break
            if len(run) == len(key):
                # The window must stretch to hold a run longer than tail_words itself.
                if i >= len(tokens) - max(tail_words, j - i + 1):
                    return True
                break
            if not split_words and len(run) not in bounds:
                break
    return False


def _phrase_in_tail(text: str, phrases: object, tail_words: int, *, split_words: bool) -> bool:
    """True when any phrase is spelled by a run of words inside the trailing window."""

    if not isinstance(phrases, (list, tuple, set, frozenset)):
        return False
    tokens = _words(text)
    if not tokens:
        return False
    return any(
        isinstance(phrase, str)
        and _spells_phrase_in_tail(tokens, _words(phrase), tail_words, split_words=split_words)
        for phrase in phrases
    )


def is_confirmation(
    text: str,
    phrases: object = DEFAULT_CONFIRMATION_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    """True when ``text`` ends with a phrase that should fire the buffered turn.

    Matching ignores case, punctuation, hyphens, apostrophes and the spaces
    between the phrase's words, and is anchored to the trailing ``tail_words``
    words, so "go ahead and tell me what you think about the plan" does not
    execute — only a trailing "go ahead" does.

    A confirmation is matched more strictly than an exit: its words may run
    together ("Sendit.") but a word of the phrase is never assembled from two
    words that were said ("Goa head" is not "go ahead"). A false confirmation
    sends a half-finished thought; a false exit only discards one.
    """

    if not isinstance(text, str) or not text.strip():
        return False
    return _phrase_in_tail(text, phrases, tail_words, split_words=False)


def is_exit(
    text: str,
    phrases: object = DEFAULT_EXIT_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    """True when ``text`` ends with a phrase that should clear the buffer."""

    if not isinstance(text, str) or not text.strip():
        return False
    return _phrase_in_tail(text, phrases, tail_words, split_words=True)


def is_echo(
    transcript: str, last_tts_text: str, *, min_run: int = ECHO_MIN_RUN
) -> bool:
    if (
        not all(isinstance(text, str) for text in (transcript, last_tts_text))
        or min_run < 1
    ):
        return False
    shorter, longer = sorted((_words(transcript), _words(last_tts_text)), key=len)
    if len(shorter) < min_run:
        return False
    candidates = set(_runs(shorter, min_run))
    return any(run in candidates for run in _runs(longer, min_run))


def _domain(match: re.Match[str]) -> str:
    host = (match.group(1) or match.group(2) or "").rpartition("@")[2].partition(":")[0]
    return host[4:] if host[:4].lower() == "www." else host


def _filename(match: re.Match[str]) -> str:
    trimmed = match.group(0).rstrip("/")
    return trimmed.rpartition("/")[2] or trimmed


_SPEECH_STAGES = (
    (_FENCE_RE, _CODE_BLOCK_SPOKEN),
    (_OPEN_FENCE_RE, _CODE_BLOCK_SPOKEN),
    (_MD_LINK_RE, r"\1"),
    (_INLINE_CODE_RE, r"\1"),
    (_URL_RE, _domain),
    (_FLAG_RE, " "),
    (_PATH_RE, _filename),
    (_RULE_RE, " "),
    (_HEADING_RE, ""),
    (_QUOTE_RE, ""),
    (_BULLET_RE, ""),
    (_EMPHASIS_RE, r"\2"),
    (re.compile(r"\|"), " "),
    (re.compile(r"\s+"), " "),
    (_SPACE_BEFORE_PUNCT_RE, r"\1"),
    (_REPEATED_PUNCT_RE, r"\1"),
)


def clean_for_speech(text: str) -> str:
    if not isinstance(text, str):
        return ""
    projected = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern, replacement in _SPEECH_STAGES:
        projected = pattern.sub(replacement, projected)
    return projected.strip()

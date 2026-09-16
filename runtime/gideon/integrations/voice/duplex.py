"""Token-window decisions and speech-only text projection for duplex voice."""

import re
from collections import defaultdict, deque
from collections.abc import Iterator

TAIL_WINDOW_WORDS = 6


ECHO_MIN_RUN = 3

DEFAULT_CONFIRMATION_PHRASES: tuple[str, ...] = (
    "do it",
    "go ahead",
    "send it",
    "execute",
)

DEFAULT_EXIT_PHRASES: tuple[str, ...] = (
    "cancel",
    "never mind",
    "forget it",
)


VOICE_DISCLAIMER = "(Transcribed from voice; transcription may be inaccurate.)"


DEFAULT_PUSH_TO_TALK_CHORD = "CommandOrControl+Shift+Space"

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")

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
    return [match.group(0) for match in _WORD_RE.finditer(text.lower())]


def _runs(tokens: list[str], width: int) -> Iterator[tuple[str, ...]]:
    window: deque[str] = deque(maxlen=width)
    for token in tokens:
        window.append(token)
        if len(window) == width:
            yield tuple(window)


def _phrase_in_tail(text: str, phrases: object, tail_words: int) -> bool:
    if not isinstance(phrases, (list, tuple, set, frozenset)):
        return False
    vocabulary: dict[int, set[tuple[str, ...]]] = defaultdict(set)
    for phrase in phrases:
        if isinstance(phrase, str):
            words = tuple(_words(phrase))
            if words:
                vocabulary[len(words)].add(words)
    utterance = _words(text)
    for width, choices in vocabulary.items():
        tail = utterance[-max(width, tail_words) :]
        if any(run in choices for run in _runs(tail, width)):
            return True
    return False


def _matches(text: str, phrases: object, tail_words: int) -> bool:
    return (
        isinstance(text, str)
        and bool(text.strip())
        and _phrase_in_tail(text, phrases, tail_words)
    )


def is_confirmation(
    text: str,
    phrases: object = DEFAULT_CONFIRMATION_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    return _matches(text, phrases, tail_words)


def is_exit(
    text: str,
    phrases: object = DEFAULT_EXIT_PHRASES,
    *,
    tail_words: int = TAIL_WINDOW_WORDS,
) -> bool:
    return _matches(text, phrases, tail_words)


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

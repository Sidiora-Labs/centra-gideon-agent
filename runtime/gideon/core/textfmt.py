"""Extract optional response annotations while retaining user-visible model text."""

import re

_OPTIONS_RE = re.compile(r"\[OPTIONS:\s*(.+?)\]\s*$", re.MULTILINE)
_THINKING_TAG_RE = re.compile(
    r"<(?:thinking|antml:thinking)>(.*?)</(?:thinking|antml:thinking)>", re.DOTALL
)


def extract_options(text: str) -> tuple[str, list[str]]:
    marker = next(_OPTIONS_RE.finditer(text), None)
    if marker is None:
        return text, []
    choices = filter(None, map(str.strip, marker.group(1).split("|")))
    return text[: marker.start()].rstrip(), list(choices)


def strip_thinking_tags(text: str, *, strip_whitespace: bool = True) -> tuple[str, str]:
    thoughts = []

    def consume(match):
        thought = match.group(1).strip()
        if thought:
            thoughts.append(thought)
        return ""

    visible = _THINKING_TAG_RE.sub(consume, text)
    return visible.strip() if strip_whitespace else visible, "\n\n".join(thoughts)

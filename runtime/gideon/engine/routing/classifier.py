"""Deterministic request signals for model selection."""

import re
from dataclasses import dataclass

SHORT_CHAT = "short_chat"
CODE = "code"
SUMMARIZE = "summarize"
EXTRACT_STRUCTURED = "extract_structured"
LONG_REASONING = "long_reasoning"
QUERY_CLASSES: tuple[str, ...] = (
    SHORT_CHAT,
    CODE,
    SUMMARIZE,
    EXTRACT_STRUCTURED,
    LONG_REASONING,
)
CLASSIFIER_VERSION = 1
_SHORT_MAX = 240
_LONG_MIN = 2000
_CODE_FENCE = re.compile("```|~~~")
_CODE_SIGNAL = re.compile(
    "\\bdef\\s+\\w+\\s*\\(|\\bclass\\s+\\w+\\s*[:(]|\\bfunction\\s+\\w+\\s*\\(|\\bfunction\\s*\\(|\\b(const|let|var)\\s+\\w+\\s*=|^\\s*(import|from)\\s+[\\w.]|\\bSELECT\\b.*\\bFROM\\b|[{};]\\s*$|=>|::|</?[a-z][\\w-]*>|\\bstack ?trace\\b|\\btraceback\\b",
    re.MULTILINE | re.IGNORECASE,
)
_SUMMARIZE_SIGNAL = re.compile(
    "\\b(summar(?:ise|ize|y)|tl;?dr|recap|condense|digest|key points|in (?:a few|one|two|three) (?:sentences|words|bullets)|boil(?: it)? down|the gist)\\b",
    re.IGNORECASE,
)
_STRUCTURED_SIGNAL = re.compile(
    "\\b(json|yaml|csv|as a table|valid schema|json schema|return (?:a|an|the) (?:object|array|list|dict)|structured (?:output|response|data)|key[- ]value pairs?)\\b",
    re.IGNORECASE,
)
_REASONING_SIGNAL = re.compile(
    "\\b(step[- ]by[- ]step|reason through|think through|analy[sz]e|trade[- ]?offs?|pros and cons|prove|derive|explain why|root cause|design a|architect|compare and contrast|evaluate the)\\b",
    re.IGNORECASE,
)
_CODE_USE_CASES = frozenset({"code_tools"})
_REASONING_USE_CASES = frozenset({"reasoning"})


@dataclass(frozen=True)
class QuerySignals:
    body: str
    use_case: str
    structured: bool

    def candidates(self):
        trimmed = self.body.strip()
        yield EXTRACT_STRUCTURED, lambda: self.structured or _STRUCTURED_SIGNAL.search(
            trimmed
        )
        yield CODE, lambda: (
            _CODE_FENCE.search(self.body)
            or self.use_case in _CODE_USE_CASES
            or _CODE_SIGNAL.search(self.body)
        )
        yield SUMMARIZE, lambda: _SUMMARIZE_SIGNAL.search(trimmed)
        yield LONG_REASONING, lambda: (
            len(trimmed) >= _LONG_MIN
            or self.use_case in _REASONING_USE_CASES
            or _REASONING_SIGNAL.search(trimmed)
        )

    def choose(self):
        return next(
            (name for name, matches in self.candidates() if matches()), SHORT_CHAT
        )


def classify_query(
    text: str, use_case: str = "", *, wants_structured_output: bool = False
) -> str:
    return QuerySignals(text or "", use_case, wants_structured_output).choose()

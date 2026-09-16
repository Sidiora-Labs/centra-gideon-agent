"""What must never reach an append-only file, and what must never sit inline in one.

Two screens, both applied on the way IN because a ledger is append-only: a credential written to
`journal.jsonl` is a credential in every bug report and every flywheel read from then on, and there
is no line to go back and edit. Same for a binary blob — a reader that has already parsed it has
already paid for it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def redact(value: Any) -> Any:
    """Strip credentials from anything bound for the journal.

    Delegates to the platform's existing redactors rather than re-deriving patterns:
    they are already maintained, already cover the exfiltration-URL case, and a second
    private copy of the rules would drift out of date exactly when it mattered.
    """
    if isinstance(value, str):
        try:
            from gideon.security.security import (
                redact_credentials,
                redact_exfiltration_urls,
            )

            text, _ = redact_exfiltration_urls(value)
            text, _ = redact_credentials(text)
            return text
        except Exception:  # pragma: no cover — redaction must never break a write
            logger.debug("redaction unavailable", exc_info=True)
            return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"\x1f\x8b",
    b"PK\x03\x04",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"\x7fELF",
    b"OggS",
    b"RIFF",
)

_BASE64_PREFIXES: tuple[str, ...] = (
    "iVBORw0KGgo",
    "/9j/",
    "R0lGODdh",
    "R0lGODlh",
    "JVBERi0",
    "H4sI",
    "UEsDBB",
    "f0VMRg",
)


def is_binary_payload(value: Any) -> bool:
    """True when ``value`` is a string whose leading bytes match a known binary format.

    Content-based, so it catches a small binary an inline-size check never would: a 400-byte
    PNG is under every threshold and still meaningless inline — mojibake in the widget, a
    poisoned `{{nodes.x.output}}` binding, wasted context if it reaches a model.

    Both carriers are checked. Raw bytes decoded into a `str` are recovered with latin-1
    (which maps codepoints 0-255 back to the identical bytes) rather than UTF-8 — a PNG's
    leading `\\x89` UTF-8-encodes to TWO bytes, so a UTF-8 round-trip silently fails to match
    any magic number, which is exactly the bug this comment exists to prevent. Base64 is the
    other carrier, and in practice the more common one.

    Only strings are inspected. A dict or list is structure the engine created, and treating
    a container as binary because one leaf looked like a PNG would spill a whole useful
    output over one field.
    """
    if not isinstance(value, str) or not value:
        return False
    head = value[:16]
    try:
        raw = head.encode("latin-1")
    except UnicodeEncodeError:
        raw = head.encode("utf-8", errors="surrogateescape")
    if any(raw.startswith(m) for m in _MAGIC_PREFIXES):
        return True
    return value[:16].startswith(_BASE64_PREFIXES)

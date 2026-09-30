"""Redaction for credential-bearing scp-style remote addresses."""

from __future__ import annotations

import re


_ADDRESS_LOGIN_RE = re.compile(
    r"(?<![A-Za-z0-9_.+-])"
    r"(?P<login>[A-Za-z0-9._+-]+:[^@\s]+)@"
    r"(?P<host>(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\])):"
    r"(?P<path>\S+)"
)


def redact_address_logins(text: str) -> tuple[str, list[str]]:
    """Remove login material only from credential-bearing ``user:password@host:path`` forms."""
    warnings: list[str] = []

    def replace(match: re.Match[str]) -> str:
        warnings.append("Redacted credential in scp-style address")
        return f"[REDACTED: address credential]@{match['host']}:{match['path']}"

    return _ADDRESS_LOGIN_RE.sub(replace, text), warnings

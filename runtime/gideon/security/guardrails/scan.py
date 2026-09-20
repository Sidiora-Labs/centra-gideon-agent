"""Outbound secret/PII scan at the model-call seam (AUTONOMY-GUARDRAILS §2.2).

The network egress chokepoint guards the *transport*; this guards the *content*.
Every outbound prompt bound for a REMOTE provider passes the scan before it leaves
the machine. Local-only providers skip to ``warn`` (the content never leaves).

Builds on what already exists rather than reinventing detection:

* ``security.redact_credentials`` / ``redact_exfiltration_urls`` supply the
  credential + exfil-URL passes (AWS keys, private keys, Slack tokens, base64
  variants, suspicious query strings).
* A small PII pass adds email + phone + long key-shaped strings.

The mode ladder (per ``GuardrailsConfig.scan_mode``, but forced to ``warn`` for
local providers):

* ``warn``   — log the findings + proceed with the ORIGINAL prompt.
* ``redact`` — substitute the findings out, proceed with the CLEANED prompt.
* ``block``  — refuse the call (the caller raises ``SecretLeakBlocked``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from gideon.security.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d|\(\d)[\d\s().-]{7,}\d(?!\d)")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_ISO_DATE_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?(?!\d)"
)
_DECIMAL_RE = re.compile(r"(?<![\d.])\d+\.\d+(?![\d.])")


@dataclass
class ScanResult:
    """The outcome of scanning one outbound prompt."""

    text: str
    findings: int
    blocked: bool = False
    categories: tuple[str, ...] = ()
    injection: bool = False
    injection_group: str = ""


def _phone_spans(text: str) -> list[tuple[int, int]]:
    masked = list(text)
    spared: list[tuple[int, int]] = []
    for match in _IPV4_RE.finditer(text):
        if all(0 <= int(octet) <= 255 for octet in match.group().split(".")):
            spared.append(match.span())
    for match in _ISO_DATE_RE.finditer(text):
        try:
            datetime.fromisoformat(match.group().replace("Z", "+00:00"))
        except ValueError:
            continue
        spared.append(match.span())
    spared.extend(match.span() for match in _DECIMAL_RE.finditer(text))
    for start, end in spared:
        masked[start:end] = "#" * (end - start)
    return [match.span() for match in _PHONE_RE.finditer("".join(masked))]


def _count_pii(text: str) -> tuple[int, list[str]]:
    cats: list[str] = []
    n = 0
    emails = _EMAIL_RE.findall(text)
    if emails:
        n += len(emails)
        cats.append("email")
    phones = _phone_spans(text)
    if phones:
        n += len(phones)
        cats.append("phone")
    return n, cats


def _redact_pii(text: str) -> str:
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    for start, end in reversed(_phone_spans(text)):
        text = text[:start] + "[REDACTED_PHONE]" + text[end:]
    return text


def scan_outbound(text: str, *, mode: str) -> ScanResult:
    """Scan ``text`` for secrets/PII and apply the ``mode`` ladder.

    ``mode`` is ``warn`` | ``redact`` | ``block`` (an unknown value is treated as
    ``warn`` — the least surprising, never a silent hard block). Detection always
    runs; the mode only decides what happens to a finding.
    """
    if not text:
        return ScanResult(text=text, findings=0)

    inj_group = ""
    try:
        from gideon.automation.triggers.screen import screen as _screen

        verdict = _screen(text)
        if verdict.blocked:
            inj_group = verdict.matched_group or "injection"
    except (
        Exception
    ):  # noqa: BLE001 - a screen failure must not wedge every outbound call
        logger.debug(
            "outbound injection screen failed; continuing with secret/PII scan",
            exc_info=True,
        )

    cleaned_cred, cred_warnings = redact_credentials(text)
    cleaned_both, url_warnings = redact_exfiltration_urls(cleaned_cred)
    pii_count, pii_cats = _count_pii(text)

    findings = (
        len(cred_warnings) + len(url_warnings) + pii_count + (1 if inj_group else 0)
    )
    categories: list[str] = []
    if inj_group:
        categories.append("injection")
    if cred_warnings:
        categories.append("credential")
    if url_warnings:
        categories.append("exfil_url")
    categories.extend(pii_cats)

    if findings == 0:
        return ScanResult(text=text, findings=0)

    mode = mode if mode in ("warn", "redact", "block") else "warn"
    if mode == "block":
        logger.warning(
            "outbound scan: %d finding(s) → BLOCK (%s)", findings, ",".join(categories)
        )
        return ScanResult(
            text=text,
            findings=findings,
            blocked=True,
            categories=tuple(categories),
            injection=bool(inj_group),
            injection_group=inj_group,
        )
    if mode == "redact":
        return ScanResult(
            text=_redact_pii(cleaned_both),
            findings=findings,
            categories=tuple(categories),
            injection=bool(inj_group),
            injection_group=inj_group,
        )
    logger.info(
        "outbound scan: %d finding(s) → WARN (%s)", findings, ",".join(categories)
    )
    return ScanResult(
        text=text,
        findings=findings,
        categories=tuple(categories),
        injection=bool(inj_group),
        injection_group=inj_group,
    )

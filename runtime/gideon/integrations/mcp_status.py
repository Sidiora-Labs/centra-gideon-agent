"""Shared, sanitized MCP startup outcomes and change notifications."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

STOP_AFTER = 3
STDERR_TAIL_BYTES = 8192
logger = logging.getLogger(__name__)
_listeners: set[Callable[[str], None]] = set()


def safe_text(text: str) -> str:
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return redact_credentials(redact_exfiltration_urls(text)[0])[0]


def cause_line(stderr: str) -> str:
    lines = [line.strip().lstrip("×✖✗•·│├└╰─▶>|*- ") for line in safe_text(stderr).splitlines()]
    lines = [line for line in lines if line and not re.match(
        r'^(File ".*", line \d+|at \S|Traceback \(|During handling|The above exception|hint:|help:|note:|[\^~]+$)',
        line, re.I,
    )]
    errors = [line for line in lines if re.match(
        r"^([A-Za-z_][\w.]*(Error|Exception|Exit)\b|(error|fatal|panic|failed)\b)", line, re.I
    )]
    return ((errors or lines or [""])[-1])[:200]


@dataclass(frozen=True)
class StartFailure:
    headline: str
    detail: str = ""
    counts: bool = True
    pending: bool = False


def exited(server: str, returncode: int, stderr: str) -> StartFailure:
    detail = safe_text(stderr).strip()
    how = f"exited with code {returncode}" if returncode >= 0 else f"was ended by signal {-returncode}"
    cause = cause_line(detail)
    return StartFailure(
        f"{server} {how} before it answered" + (f": {cause.rstrip('.')}." if cause else ", and wrote no reason."),
        detail,
    )


def closed(server: str, stderr: str) -> StartFailure:
    detail = safe_text(stderr).strip()
    cause = cause_line(detail)
    return StartFailure(f"{server} closed its connection before it answered" + (f": {cause}." if cause else "."), detail)


def did_not_answer(server: str, waited: float, stderr: str = "") -> StartFailure:
    return StartFailure(f"{server} did not answer within {waited:g} seconds, so Gideon stopped it.", safe_text(stderr).strip())


def still_starting(server: str, waited: float, stderr: str = "", *, allowance: float, earlier: bool = False) -> StartFailure:
    text = (
        f"{server} is still finishing an earlier start, so it was not started a second time."
        if earlier else
        f"{server} has not answered after {waited:g} seconds and is still starting. Gideon lets its first start finish for up to {allowance / 60:g} minutes."
    )
    return StartFailure(text + " Gideon checks it again when that start ends.", safe_text(stderr).strip(), False, True)


def stopped_trying(server: str, last: str) -> str:
    return f"{server} failed to start {STOP_AFTER} times in a row, so Gideon stopped trying. The last time: {last.rstrip('.')}. Press Retry to start it again."


def subscribe(listener: Callable[[str], None]) -> None:
    _listeners.add(listener)


def unsubscribe(listener: Callable[[str], None]) -> None:
    _listeners.discard(listener)


def announce(server: str) -> None:
    for listener in tuple(_listeners):
        try:
            listener(server)
        except Exception:
            logger.debug("MCP status listener failed", exc_info=True)

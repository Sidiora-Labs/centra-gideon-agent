"""Typed workflow failure classification and current retry-window projection."""

from __future__ import annotations

import re
import socket
import time
from collections.abc import Iterable
from typing import Any

from gideon.automation.workflows.models import RETRYABLE_CLASSES, Failure, FailureClass

_CLASS_FIX = {
    FailureClass.USER: "check this step's configuration before retrying",
    FailureClass.PERMISSION: "check the credential in Settings → Providers",
    FailureClass.NETWORK: "check that the provider is running and reachable, then Retry",
    FailureClass.TRANSIENT: "Retry in a moment",
    FailureClass.TIMEOUT: "raise the step timeout, or split it into smaller steps",
    FailureClass.BUDGET: "raise the applicable budget in Settings → Guardrails",
    FailureClass.PROTOCOL: "tighten the schema in the prompt, or use produce-then-extract",
    FailureClass.INTERNAL: "check the gateway log",
}


def _failure(cls: FailureClass, cause: str, remediation: str | None = None) -> Failure:
    retryable = cls in RETRYABLE_CLASSES
    return Failure(
        failure_class=cls,
        cause_plain=cause[:500],
        remediation=remediation or _CLASS_FIX[cls],
        recoverable=retryable,
    )


def _http_status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        for name in ("status_code", "status"):
            raw = getattr(holder, name, None)
            if isinstance(raw, int) and 100 <= raw <= 599:
                return raw
    return None


def http_status_class(status: int) -> FailureClass:
    if status in (401, 402, 403):
        return FailureClass.PERMISSION
    if status in (408, 425, 429) or status >= 500:
        return FailureClass.TRANSIENT
    return FailureClass.USER


def _typed_failure(exc: BaseException) -> Failure | None:
    name = type(exc).__name__.lower()
    message = str(exc)
    if name == "circuitopenerror" and hasattr(exc, "retry_after"):
        provider = str(getattr(exc, "provider", "") or "")
        wait = max(0.0, float(getattr(exc, "retry_after", 0.0) or 0.0))
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=message[:500],
            remediation=f"Retry when the provider circuit reopens, in about {wait:.0f}s",
            recoverable=True,
            retry_at=time.time() + wait,
            providers=[provider] if provider else [],
        )
    if name in {"budgetexceedederror"}:
        return _failure(FailureClass.BUDGET, message)
    if name in {"secretleakblocked", "promptinjectionblocked"}:
        return _failure(
            FailureClass.PERMISSION,
            message,
            "resolve the guardrail finding before retrying",
        )
    if name in {"outputcontracterror"}:
        return _failure(FailureClass.PROTOCOL, message)
    if name in {"credentialmissing"}:
        return _failure(FailureClass.PERMISSION, message)
    if name in {"providerresolutionerror"}:
        agent_error = getattr(exc, "agent_error", None)
        return _failure(
            FailureClass.USER,
            message,
            str(getattr(agent_error, "fix", "") or "") or None,
        )
    status = _http_status(exc)
    if status is not None:
        cls = http_status_class(status)
        cause = f"HTTP {status}: {message}" if message else f"HTTP {status}"
        return _failure(cls, cause)
    if isinstance(exc, (socket.gaierror, ConnectionError, OSError)) and not isinstance(
        exc, FileNotFoundError
    ):
        return _failure(FailureClass.NETWORK, f"{type(exc).__name__}: {message}")
    return None


def _by_text(name: str, text: str, *, timed_out: bool = False) -> Failure:
    low = text.lower()
    cause = f"{name}: {text}"[:500] if name else text[:500]
    if timed_out or "timeout" in low or "timed out" in low:
        # An explicit timeout exception is the node's configured deadline; a provider timeout is
        # transient and can clear once the provider responds.
        cls = FailureClass.TIMEOUT if timed_out else FailureClass.TRANSIENT
        return _failure(cls, cause)
    match = re.search(r"\b(?:http\s*)?([45]\d\d)\b", low)
    if match:
        cls = http_status_class(int(match.group(1)))
        return _failure(cls, cause)
    if any(
        term in low
        for term in ("connection", "network", "dns", "unreachable", "socket")
    ):
        return _failure(FailureClass.NETWORK, cause)
    if any(
        term in low
        for term in (
            "permission",
            "forbidden",
            "unauthorized",
            "credential",
            "access denied",
        )
    ):
        return _failure(FailureClass.PERMISSION, cause)
    if any(
        term in low
        for term in (
            "rate limit",
            "429",
            "throttl",
            "overloaded",
            "capacity",
            "503",
            "502",
            "500",
        )
    ):
        return _failure(FailureClass.TRANSIENT, cause)
    if any(term in low for term in ("schema", "json", "output contract")):
        return _failure(FailureClass.PROTOCOL, cause)
    return _failure(FailureClass.INTERNAL, cause)


def classify_exception(exc: BaseException, *, use_case: str = "") -> Failure:
    """Classify typed guard/provider failures first, and unknown exceptions fail-closed."""
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            break
        typed = _typed_failure(seen)
        if typed is not None:
            typed.cause_plain = (
                typed.cause_plain or f"{type(exc).__name__}: {exc}"[:500]
            )
            return typed
        seen = seen.__cause__
    return _by_text(
        type(exc).__name__, str(exc), timed_out=isinstance(exc, TimeoutError)
    )


def classify_action_result(result: Any) -> Failure:
    """Classify an actual failed action result; unrecognized failures remain nonretryable."""
    cause = str(
        getattr(result, "error", "") or getattr(result, "stderr", "") or "action failed"
    )[:500]
    err = getattr(result, "agent_error", None)
    fix = str(getattr(err, "fix", "") or "") if err is not None else ""
    declared = str(getattr(result, "failure_class", "") or "")
    try:
        cls = FailureClass(declared) if declared else None
    except ValueError:
        cls = None
    status = getattr(result, "status_code", None) or getattr(
        result, "http_status", None
    )
    if cls is None and isinstance(status, int) and 100 <= status <= 599:
        cls = http_status_class(status)
    if cls is None:
        parsed = _by_text("", cause)
        cls = parsed.failure_class
    return Failure(
        failure_class=cls,
        cause_plain=cause,
        remediation=fix or _CLASS_FIX[cls],
        recoverable=cls in RETRYABLE_CLASSES,
        retry_at=(
            (time.time() + max(0.0, float(getattr(result, "retry_after", 0.0) or 0.0)))
            if cls in RETRYABLE_CLASSES
            and getattr(result, "retry_after", None) is not None
            else None
        ),
    )


def with_breaker_window(
    failure: Failure | None, providers: Iterable[str] = ()
) -> Failure | None:
    """Refresh retry eligibility from each named provider's live breaker on a status read."""
    if failure is None or not failure.retryable:
        return failure
    names = sorted({*(failure.providers or []), *(str(p) for p in providers if p)})
    failure.providers = names
    try:
        from gideon.security.guardrails.breaker import all_breakers

        breakers = all_breakers()
        known = [breakers[name] for name in names if name in breakers]
        wait = max((breaker.retry_after() for breaker in known), default=0.0)
    except Exception:
        return failure
    if known:
        failure.retry_at = time.time() + wait if wait > 0 else None
    return failure

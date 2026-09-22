"""Classify provider exceptions into workflow failure outcomes."""

from __future__ import annotations

import re

from gideon.automation.workflows.models import Failure, FailureClass

_PROVIDER_SERVER_NAMES = frozenset(
    {
        "internalserverexception",
        "internalserviceexception",
        "serviceunavailableexception",
        "serviceexception",
    }
)
_PROVIDER_SERVER_TEXT = re.compile(
    r"\b(?:internal ?server (?:error|exception)|service unavailable|"
    r"(?:http )?5\d\d|bad gateway|gateway error)\b",
    re.IGNORECASE,
)


def classify_exception(exc: BaseException) -> Failure:
    """Map a provider exception to the workflow retry taxonomy."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if isinstance(exc, TimeoutError) or "timeout" in low or "timed out" in low:
        return Failure(
            failure_class=FailureClass.TIMEOUT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="raise timeout_total, or split the node into smaller steps",
            recoverable=True,
        )
    if any(k in low for k in ("connection", "network", "dns", "unreachable", "socket")):
        return Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check connectivity; the engine will retry",
            recoverable=True,
        )
    if any(
        k in low
        for k in (
            "permission",
            "forbidden",
            "unauthorized",
            "credential",
            "access denied",
        )
    ):
        return Failure(
            failure_class=FailureClass.PERMISSION,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check the credential in Settings → Providers, or the tool's "
            "approval policy",
        )
    if any(
        k in low for k in ("rate limit", "429", "throttl", "overloaded", "capacity")
    ):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="the engine will back off and retry",
            recoverable=True,
        )
    if name.lower() in _PROVIDER_SERVER_NAMES or _PROVIDER_SERVER_TEXT.search(text):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="the provider server failed; the engine will retry",
            recoverable=True,
        )
    if "outputcontract" in name.lower() or "schema" in low or "json" in low:
        return Failure(
            failure_class=FailureClass.PROTOCOL,
            cause_plain=f"{name}: {text}"[:500],
            remediation="tighten the schema in the prompt, or use produce-then-extract",
        )
    return Failure(
        failure_class=FailureClass.INTERNAL,
        cause_plain=f"{name}: {text}"[:500],
        remediation="check the gateway log for the full traceback",
    )

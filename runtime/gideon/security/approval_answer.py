"""Principal-bound approval answers shared by dashboard and workflow consumers."""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

OWNER = "owner"
CHANNEL = "channel"
APP = "app"
AGENT = "agent"
BRIDGE = "bridge"
TRIGGER = "trigger"
RUN = "run"
UNKNOWN = "unknown"
ASKER_REFUSAL = "The party that asked for this approval cannot answer it."


@dataclass(frozen=True)
class Principal:
    kind: str
    name: str = ""
    tenant: str = ""

    @property
    def label(self) -> str:
        identity = f"{self.tenant}/{self.name}" if self.tenant else self.name
        return f"{self.kind}:{identity}" if identity else self.kind


YOU = Principal(OWNER)


def on_channel(provider: str, owner_id: str = "", tenant: str = "") -> Principal:
    return Principal(CHANNEL, owner_id or provider, tenant)


def app(name: str) -> Principal:
    return Principal(APP, name)


def agent(session_key: str = "") -> Principal:
    return Principal(AGENT, session_key)


def bridge(client_id: str = "") -> Principal:
    return Principal(BRIDGE, client_id or "surface")


def trigger(trigger_id: str = "") -> Principal:
    return Principal(TRIGGER, trigger_id)


def run(run_id: str) -> Principal:
    return Principal(RUN, run_id)


def asker_of_chat(session_key: str, *, created_by_app: str = "") -> Principal:
    return app(created_by_app) if created_by_app else agent(session_key)


def of_request(request: Any) -> Principal:
    """Return only identity established by the active authentication boundary."""
    app_name = str(request.get("app") or "")
    if app_name:
        return app(app_name)
    supplied = getattr(request, "headers", {}).get("X-Internal-Secret", "")
    expected = getattr(request, "app", {}).get("local_secret", "")
    if supplied and expected and hmac.compare_digest(str(supplied), str(expected)):
        return agent(str(request.headers.get("X-Session-Key", "") or "").strip())
    if request.get("user"):
        return Principal(OWNER, str(request.get("user")))
    return Principal(UNKNOWN)


def refusal(by: Principal, *, asked_by: str = "", event: bool = False) -> str:
    allowed = {OWNER, CHANNEL}
    if event:
        allowed.add(TRIGGER)
    if by.kind not in allowed:
        return "Only the owner answers an approval, in the dashboard or on their paired channel."
    if asked_by and by.label == asked_by:
        return ASKER_REFUSAL
    return ""


def refuse(by: Principal, *, what: str, asked_by: str, why: str) -> None:
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=by.label,
            operation="approval.answer_refused",
            outcome="denied",
            source="approval_answer",
            resources=f"{what} asked_by={asked_by or 'unknown'}"[:300],
            error=why[:300],
        )
    except Exception:
        logger.warning("approval refusal audit failed", exc_info=True)


def check(by: Principal, *, what: str, asked_by: str, event: bool = False) -> str:
    why = refusal(by, asked_by=asked_by, event=event)
    if why:
        refuse(by, what=what, asked_by=asked_by, why=why)
    return why


def principal_record(principal: Principal) -> dict[str, str]:
    return {"kind": principal.kind, "name": principal.name, "tenant": principal.tenant}


def principal_from_record(value: Any) -> Principal:
    if not isinstance(value, dict) or set(value) != {"kind", "name", "tenant"}:
        return Principal(UNKNOWN)
    if not all(isinstance(v, str) for v in value.values()):
        return Principal(UNKNOWN)
    if value["kind"] not in {OWNER, CHANNEL, APP, AGENT, BRIDGE, TRIGGER, RUN}:
        return Principal(UNKNOWN)
    return Principal(**value)


def ingress_record(principal: Principal, session_key: str, own_text: str) -> dict:
    """Server-authenticated provenance; this record is evidence, never a capability."""
    record = {
        "principal": principal_record(principal),
        "source_thread": session_key,
        "source_user": principal.label,
        "source_event_id": uuid.uuid4().hex,
        "source_digest": hashlib.sha256(own_text.encode("utf-8")).hexdigest(),
    }
    from gideon.security.durable_work import sign_ingress

    return sign_ingress(record)


def work_principal_of_request(request: Any) -> Principal:
    """Work constraints can inherit proven ancestry; approval answers never do."""
    from gideon.security.session_credentials import work_of_request

    proof = work_of_request(request)
    return (
        (proof.work_actor or proof.initiator)
        if proof is not None
        else of_request(request)
    )

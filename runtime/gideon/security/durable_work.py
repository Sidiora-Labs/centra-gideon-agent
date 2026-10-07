"""Authenticated source receipts for already-accepted background work.

Receipts prove source provenance only; live policy and native capabilities remain
mandatory at every operation. Imported labels and replacement signing keys cannot
create that provenance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from gideon.security.approval_answer import OWNER, Principal, principal_from_record
from gideon.security.session_credentials import begin_turn, current_work, end_turn

_DOMAIN = b"gideon.accepted-ingress.v1\0"
_FIELDS = (
    "principal",
    "source_thread",
    "source_user",
    "source_event_id",
    "source_digest",
)


def _row_ingress(row: dict) -> dict:
    meta = row.get("meta")
    ingress = meta.get("ingress") if isinstance(meta, dict) else None
    return ingress if isinstance(ingress, dict) else {}


def _payload(ingress: dict) -> bytes:
    return json.dumps(
        {key: ingress[key] for key in _FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def sign_ingress(ingress: dict) -> dict:
    from gideon.security.session_signing import load_or_create_key

    signature = hmac.new(
        load_or_create_key(), _DOMAIN + _payload(ingress), hashlib.sha256
    ).hexdigest()
    return {**ingress, "origin_proof": signature}


def verified_ingress(ingress) -> bool:
    from gideon.security.session_signing import KEY_BYTES, key_path

    if not isinstance(ingress, dict) or not isinstance(
        ingress.get("origin_proof"), str
    ):
        return False
    try:
        key = key_path().read_bytes()
        if len(key) < KEY_BYTES:
            return False
        expected = hmac.new(
            key, _DOMAIN + _payload(ingress), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, ingress["origin_proof"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _owner_source(log, key: str):
    """Read the actual canonical transcript; no caller-supplied metadata is authority."""
    canonical = log._canonical_key(key)
    metadata = log.get_metadata(key)
    actor = principal_from_record(metadata.get("initiator"))
    if (
        actor.kind != OWNER
        or metadata.get("memory_mode") != "persistent"
        or metadata.get("closed")
        or metadata.get("lifecycle", "active") != "active"
        or metadata.get("created_by_app")
        or metadata.get("app")
    ):
        return None
    rows = log._read_messages(key)
    source = None
    # Mixed foreign user work cannot teach outcomes through an owner session.
    for row in rows:
        if row.get("role") != "user":
            continue
        ingress = _row_ingress(row)
        if (
            not verified_ingress(ingress)
            or principal_from_record(ingress.get("principal")) != actor
            or log._canonical_key(ingress.get("source_thread", "")) != canonical
            or hashlib.sha256(str(row.get("content", "")).encode()).hexdigest()
            != ingress.get("source_digest")
        ):
            return None
        source = ingress
    return source


def validate_background_work(log, key: str, work) -> bool:
    if work is None or not work.turn_id.startswith("durable-ingress:"):
        return True
    source = _owner_source(log, key)
    if source is None or work.initiator != principal_from_record(source["principal"]):
        return False
    event_id = work.turn_id.removeprefix("durable-ingress:")
    return any(
        _row_ingress(row).get("source_event_id") == event_id
        for row in log._read_messages(key)
        if row.get("role") == "user"
    )


@contextmanager
def background_work(log, key: str):
    """Reissue only from signed canonical source; never resurrect an expired bearer."""
    existing = current_work()
    credential = None
    source = _owner_source(log, key)
    if source is not None:
        actor = principal_from_record(source["principal"])
        same_owner = (
            existing is not None
            and existing.initiator == actor
            and not existing.turn_id.startswith("durable-ingress:")
            and (existing.work_actor or existing.initiator).kind == OWNER
            and not existing.created_by_app
            and existing.memory_mode == "persistent"
            and log._canonical_key(existing.origin_session_key)
            == log._canonical_key(source["source_thread"])
        )
        if existing is None or same_owner:
            credential = begin_turn(
                "consolidation:" + log._canonical_key(key),
                principal_from_record(source["principal"]),
                turn_id="durable-ingress:" + source["source_event_id"],
                origin_session_key=source["source_thread"],
                memory_mode="persistent",
                ingress_event_id=source["source_event_id"],
                ingress_digest=source["source_digest"],
            )
    try:
        yield
    finally:
        end_turn(credential)


_RUN_DOMAIN = b"gideon.accepted-workflow-origin.v1\0"
RUN_ORIGIN_KEY = "accepted_work_origin"


@dataclass(frozen=True)
class AcceptedWorkOrigin:
    payload: str
    signature: str


def _seal_origin(values: dict) -> AcceptedWorkOrigin:
    from gideon.security.session_signing import load_or_create_key

    payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return AcceptedWorkOrigin(
        payload,
        hmac.new(
            load_or_create_key(), _RUN_DOMAIN + payload.encode(), hashlib.sha256
        ).hexdigest(),
    )


def _origin_values(origin: AcceptedWorkOrigin | None) -> dict | None:
    from gideon.security.session_signing import KEY_BYTES, key_path

    if (
        not isinstance(origin, AcceptedWorkOrigin)
        or not isinstance(origin.payload, str)
        or not isinstance(origin.signature, str)
    ):
        return None
    try:
        key = key_path().read_bytes()
        expected = hmac.new(
            key, _RUN_DOMAIN + origin.payload.encode(), hashlib.sha256
        ).hexdigest()
        if len(key) < KEY_BYTES or not hmac.compare_digest(expected, origin.signature):
            return None
        values = json.loads(origin.payload)
        return values if isinstance(values, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def accepted_origin_of_request(request) -> AcceptedWorkOrigin | None:
    from gideon.security.approval_answer import (
        APP,
        UNKNOWN,
        of_request,
        principal_record,
    )
    from gideon.security.session_credentials import work_of_request

    proof = work_of_request(request)
    actor = proof.initiator if proof is not None else of_request(request)
    if actor.kind == UNKNOWN:
        return None
    if proof is None and actor.kind not in {OWNER, APP}:
        return None
    from gideon.extensions.apps.app_work import from_request

    app = from_request(request)
    effective = (
        Principal(APP, app.app)
        if app is not None
        else (proof.work_actor or actor if proof is not None else actor)
    )
    mode = (
        proof.memory_mode
        if proof is not None
        else ("persistent" if actor.kind == OWNER and app is None else "temporary")
    )
    trigger_values = (
        _origin_values(proof.trigger_origin)
        if proof is not None and isinstance(proof.trigger_origin, AcceptedWorkOrigin)
        else None
    )
    return _seal_origin(
        {
            **(
                {"trigger_acceptance": trigger_values["trigger_acceptance"]}
                if trigger_values
                else {}
            ),
            "run_id": "",
            "initiator": principal_record(actor),
            "work_actor": principal_record(effective),
            "origin_session_key": proof.origin_session_key if proof else "",
            "memory_mode": mode,
            "created_by_app": app.app if app is not None else "",
            "event_id": uuid.uuid4().hex,
            "origin_turn_id": proof.turn_id if proof else "",
            "ingress_event_id": proof.ingress_event_id if proof else "",
            "ingress_digest": proof.ingress_digest if proof else "",
            "execution_model": proof.execution_model if proof else "",
            "execution_runtime": proof.execution_runtime if proof else "",
            "allowed_models": list(proof.allowed_models) if proof else [],
        }
    )


def bind_run_origin(run, origin: AcceptedWorkOrigin | None) -> bool:
    values = _origin_values(origin)
    if values is None or values.get("run_id") not in {"", run.id}:
        return False
    if "trigger_acceptance" in values and not _live_trigger_acceptance(values):
        return False
    from gideon.automation.workflows import ownership

    mode = ownership.run_mode(run).value
    mode = "persistent" if mode == "normal" else mode
    # A source's privacy may only be attenuated at the run boundary.
    if values.get("memory_mode") == "temporary" and mode != "temporary":
        return False
    if values.get("memory_mode") == "incognito" and mode == "persistent":
        return False
    from gideon.extensions.apps.app_work import from_record

    app = from_record(run.extra)
    if (app.app if app else "") != values.get("created_by_app", ""):
        return False
    values = {**values, "run_id": run.id, "memory_mode": mode}
    receipt = _seal_origin(values)
    run.extra = {
        **run.extra,
        RUN_ORIGIN_KEY: {"payload": receipt.payload, "signature": receipt.signature},
    }
    return True


def reviewed_origin_for_run(
    run, origin: AcceptedWorkOrigin | None
) -> AcceptedWorkOrigin | None:
    """An explicit owner review may start canonical app work, preserving its actor."""
    values = _origin_values(origin)
    if values is None or principal_from_record(values.get("work_actor")).kind != OWNER:
        return origin
    from gideon.extensions.apps.app_work import from_record
    from gideon.security.approval_answer import APP, principal_record

    app = from_record(run.extra)
    if app is None:
        return origin
    if not app.current_tier():
        return None
    return _seal_origin(
        {
            **values,
            "created_by_app": app.app,
            "work_actor": principal_record(Principal(APP, app.app)),
        }
    )


def recorded_run_origin(run) -> dict | None:
    """Signed native run attribution for audit/retirement, never execution admission."""
    if run is None:
        return None
    record = run.extra.get(RUN_ORIGIN_KEY)
    if not isinstance(record, dict):
        return None
    values = _origin_values(
        AcceptedWorkOrigin(record.get("payload", ""), record.get("signature", ""))
    )
    if values is None or values.get("run_id") != run.id:
        return None
    from gideon.automation.workflows import ownership

    mode = ownership.run_mode(run).value
    mode = "persistent" if mode == "normal" else mode
    if values.get("memory_mode") != mode:
        return None
    from gideon.extensions.apps.app_work import from_record

    app = from_record(run.extra)
    if (app.app if app else "") != values.get("created_by_app", ""):
        return None
    return values


def verified_run_origin(run) -> dict | None:
    if run is None or run.is_terminal:
        return None
    values = recorded_run_origin(run)
    if values is None:
        return None
    if "trigger_acceptance" in values and not _live_trigger_acceptance(values):
        return None
    from gideon.extensions.apps.app_work import from_record

    app = from_record(run.extra)
    if app is not None and not app.current_tier():
        return None
    return values


def workflow_proof_valid(work) -> bool:
    from gideon.automation.workflows import store

    values = verified_run_origin(store.get(work.durable_run_id))
    return (
        values is not None
        and principal_from_record(values.get("initiator")) == work.initiator
        and work.durable_event_id == values.get("event_id", "")
        and values.get("memory_mode") == work.memory_mode
        and values.get("created_by_app", "") == work.created_by_app
        and values.get("execution_model", "") == work.execution_model
        and values.get("execution_runtime", "") == work.execution_runtime
        and tuple(values.get("allowed_models", ())) == work.allowed_models
    )


@contextmanager
def workflow_work(run_id: str, node_id: str):
    from gideon.automation.workflows import ownership, store
    from gideon.security.approval_answer import APP, RUN

    work = current_work()
    key = ownership.owned_key(run_id, node_id)
    if work is not None and work.durable_run_id == run_id and work.session_key == key:
        yield
        return
    values = verified_run_origin(store.get(run_id))
    if values is None:
        raise PermissionError(
            "workflow source proof unavailable; an authenticated owner must review and resume this run"
        )
    actor = principal_from_record(values["work_actor"])
    effective = actor if actor.kind == APP else Principal(RUN, run_id)
    credential = begin_turn(
        key,
        principal_from_record(values["initiator"]),
        turn_id="workflow:" + values["event_id"],
        origin_session_key=values["origin_session_key"] or key,
        memory_mode=values["memory_mode"],
        created_by_app=values["created_by_app"],
        work_actor=effective,
        durable_run_id=run_id,
        ingress_event_id=values.get("ingress_event_id", ""),
        ingress_digest=values.get("ingress_digest", ""),
        durable_event_id=values["event_id"],
        execution_model=values.get("execution_model", ""),
        execution_runtime=values.get("execution_runtime", ""),
        allowed_models=tuple(values.get("allowed_models", ())),
    )
    try:
        yield
    finally:
        end_turn(credential)


def persist_accepted_origin(origin: AcceptedWorkOrigin | None) -> dict | None:
    """Persist only an authenticated receipt; it supplies provenance, not start consent."""
    if origin is None or _origin_values(origin) is None:
        return None
    return {"payload": origin.payload, "signature": origin.signature}


def restore_accepted_origin(record) -> AcceptedWorkOrigin | None:
    """A copied record is not authority unless this machine's signature still verifies."""
    if not isinstance(record, dict) or set(record) != {"payload", "signature"}:
        return None
    receipt = AcceptedWorkOrigin(record["payload"], record["signature"])
    values = _origin_values(receipt)
    if values is None or values.get("memory_mode") not in {
        "persistent",
        "incognito",
        "temporary",
    }:
        return None
    from gideon.security.approval_answer import UNKNOWN

    if principal_from_record(values.get("initiator")).kind == UNKNOWN:
        return None
    app_name = values.get("created_by_app")
    if app_name:
        from gideon.extensions.apps.app_work import AppWork

        if not AppWork.for_app(app_name).current_tier():
            return None
    return receipt


def accepted_origin_values(origin: AcceptedWorkOrigin | None) -> dict | None:
    """Fresh decoded authenticated audit fields; no scope or launch permission."""
    return _origin_values(origin)


def origin_for_run(run_id: str) -> dict | None:
    from gideon.automation.workflows import store

    return verified_run_origin(store.get(run_id))


TRIGGER_ORIGIN_KEY = "_accepted_trigger_origin"


def seal_trigger_acceptance(trigger, owner) -> dict | None:
    """Called only at authenticated grant acceptance, after exact grant installation."""
    from gideon.automation.triggers import grants
    from gideon.security.approval_answer import principal_record

    if owner.kind != OWNER or not grants.is_granted(trigger):
        return None
    caps = trigger.capabilities
    values = {
        "trigger_id": trigger.id,
        "revision": grants.action_revision(trigger),
        "provider": grants.required_provider(trigger),
        "workflows": caps.get(grants.SEAL_KEY, {}).get("workflows"),
        "declared_tools": list(caps.get("tools") or []),
        "declared_providers": list(caps.get("providers") or []),
        "consent_owner": principal_record(owner),
    }
    return persist_accepted_origin(_seal_origin({"trigger_acceptance": values}))


def _live_trigger_acceptance(values) -> bool:
    from gideon.automation.triggers import grants

    acceptance = values.get("trigger_acceptance")
    if not isinstance(acceptance, dict):
        return False
    row = grants._loaded_trigger(acceptance.get("trigger_id", ""))
    if row is None or not row.ok or not grants.is_granted(row.trigger):
        return False
    trigger = row.trigger
    caps = trigger.capabilities
    expected = {
        "trigger_id": trigger.id,
        "revision": grants.action_revision(trigger),
        "provider": grants.required_provider(trigger),
        "workflows": caps.get(grants.SEAL_KEY, {}).get("workflows"),
        "declared_tools": list(caps.get("tools") or []),
        "declared_providers": list(caps.get("providers") or []),
        "consent_owner": acceptance.get("consent_owner"),
    }
    if (
        principal_from_record(expected["consent_owner"]).kind != OWNER
        or acceptance != expected
    ):
        return False
    installed = caps.get(TRIGGER_ORIGIN_KEY)
    if not isinstance(installed, dict):
        return False
    signed = _origin_values(
        AcceptedWorkOrigin(installed.get("payload", ""), installed.get("signature", ""))
    )
    return signed == {"trigger_acceptance": acceptance}


def accepted_trigger_origin(trigger_id: str) -> AcceptedWorkOrigin | None:
    """Reissue event attribution from the real store and current signed consent."""
    from gideon.automation.triggers import grants
    from gideon.security.approval_answer import TRIGGER, principal_record

    row = grants._loaded_trigger(trigger_id)
    if row is None or not row.ok:
        return None
    record = row.trigger.capabilities.get(TRIGGER_ORIGIN_KEY)
    if not isinstance(record, dict):
        return None
    values = _origin_values(
        AcceptedWorkOrigin(record.get("payload", ""), record.get("signature", ""))
    )
    if values is None or not _live_trigger_acceptance(values):
        return None
    actor = principal_record(Principal(TRIGGER, trigger_id))
    return _seal_origin(
        {
            **values,
            "run_id": "",
            "initiator": actor,
            "work_actor": actor,
            "origin_session_key": "",
            "memory_mode": "persistent",
            "created_by_app": "",
            "event_id": uuid.uuid4().hex,
            "origin_turn_id": "",
            "ingress_event_id": "",
            "ingress_digest": "",
        }
    )


def declared_memory_tool(work, tool: str) -> bool:
    """A tool grant never admits prompt recall or background capture."""
    if tool not in {"memory_recall", "memory_list", "memory_remember", "memory_forget"}:
        return False
    if work.trigger_origin is not None:
        values = (
            _origin_values(work.trigger_origin) if trigger_proof_valid(work) else None
        )
    elif work.durable_run_id:
        from gideon.automation.workflows import store

        values = verified_run_origin(store.get(work.durable_run_id))
    else:
        return False
    return bool(
        values is not None
        and values.get("event_id") == work.durable_event_id
        and principal_from_record(values.get("initiator")) == work.initiator
        and tool in values.get("trigger_acceptance", {}).get("declared_tools", [])
    )


def bound_for_trigger_origin(origin, trigger_id: str, session_key: str, app_work=None):
    """Host-only scheduled child attenuation from current signed canonical consent."""
    import time

    from gideon.extensions.apps.app_work import AppWork, for_job, intersect
    from gideon.security.approval_answer import APP, TRIGGER
    from gideon.security.session_credentials import BoundWork

    values = _origin_values(origin)
    actor = principal_from_record(values.get("initiator")) if values else None
    if (
        not session_key
        or values is None
        or actor != Principal(TRIGGER, trigger_id)
        or not _live_trigger_acceptance(values)
    ):
        return None
    canonical = for_job(trigger_id)
    if app_work is not None:
        if (
            not isinstance(app_work, AppWork)
            or canonical is None
            or canonical.app != app_work.app
        ):
            return None
        tier = app_work.current_tier()
        if not tier or intersect(canonical.current_tier(), tier) != tier:
            return None
    elif canonical is not None:
        return None
    mode = values["memory_mode"]
    if app_work is not None:
        from gideon.extensions.apps.permissions import checker_for

        checker = checker_for(app_work.app)
        if checker is None or not (
            checker.can_use_memory("shared") or checker.can_use_memory("app-scoped")
        ):
            mode = "temporary"
    effective = Principal(APP, app_work.app) if app_work is not None else actor
    return BoundWork(
        session_key,
        session_key,
        "trigger:" + values["event_id"],
        actor,
        app_work.app if app_work else "",
        mode,
        time.monotonic() + 7200,
        work_actor=effective,
        durable_event_id=values["event_id"],
        trigger_origin=origin,
    )


def trigger_proof_valid(work) -> bool:
    from gideon.extensions.apps.app_work import for_job
    from gideon.security.approval_answer import APP, TRIGGER

    values = _origin_values(work.trigger_origin)
    if values is None or not _live_trigger_acceptance(values):
        return False
    actor = principal_from_record(values.get("initiator"))
    if (
        actor.kind != TRIGGER
        or actor != work.initiator
        or values.get("event_id") != work.durable_event_id
        or (
            values.get("memory_mode") != work.memory_mode
            and work.memory_mode != "temporary"
        )
    ):
        return False
    if work.created_by_app:
        app = for_job(actor.name)
        return (
            app is not None
            and app.app == work.created_by_app
            and bool(app.current_tier())
            and work.work_actor == Principal(APP, app.app)
        )
    return (work.work_actor or work.initiator) == actor

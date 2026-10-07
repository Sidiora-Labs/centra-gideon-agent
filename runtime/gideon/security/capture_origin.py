"""Verified human-authored capture sources, separate from execution authority."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from gideon.hypermid.context import session_id_for_key
from gideon.hypermid.foundation import Digest, Id
from gideon.security.approval_answer import (
    OWNER,
    Principal,
    principal_from_record,
    principal_record,
)

_DOMAIN = b"gideon.owner-word-capture.v1\0"
_CURRENT: ContextVar[VerifiedCapture | None] = ContextVar(
    "verified_word_capture", default=None
)


@dataclass(frozen=True)
class VerifiedCapture:
    original_actor: Principal
    effective_work_actor: Principal
    origin_session_key: str
    ingress_event_id: str
    ingress_own_digest: str
    own_text: str
    history_session_id: Id
    native_source_event_id: Id
    native_source_digest: Digest
    source_bytes: bytes = field(repr=False)
    capture_kind: str = "owner_words"
    executing_turn_id: str = ""
    signature: str = field(default="", repr=False)


def _values(capture):
    return {
        "original_actor": principal_record(capture.original_actor),
        "effective_work_actor": principal_record(capture.effective_work_actor),
        "origin_session_key": capture.origin_session_key,
        "ingress_event_id": capture.ingress_event_id,
        "ingress_own_digest": capture.ingress_own_digest,
        "history_session_id": str(capture.history_session_id),
        "native_source_event_id": str(capture.native_source_event_id),
        "native_source_digest": str(capture.native_source_digest),
        "capture_kind": capture.capture_kind,
        "executing_turn_id": capture.executing_turn_id,
        "own_text_digest": hashlib.sha256(capture.own_text.encode()).hexdigest(),
        "source_bytes_digest": hashlib.sha256(capture.source_bytes).hexdigest(),
    }


def _payload(capture):
    return json.dumps(_values(capture), sort_keys=True, separators=(",", ":")).encode()


def _source(log, key, ingress_id):
    from gideon.security.durable_work import verified_ingress

    metadata = log.get_metadata(key)
    if (
        metadata.get("memory_mode") != "persistent"
        or metadata.get("closed")
        or metadata.get("lifecycle", "active") != "active"
    ):
        return None
    actor = principal_from_record(metadata.get("initiator"))
    if actor.kind != OWNER or metadata.get("created_by_app"):
        return None
    for event in log.source_events(key):
        row = event.message
        meta = row.get("meta") or {}
        ingress = meta.get("ingress") or {}
        if ingress.get("source_event_id") != ingress_id:
            continue
        # Unsupported composed/pasted projections cannot become authored-word evidence.
        if row.get("role") != "user" or any(
            "paste" in name or name in {"merged_ingress", "injected", "replay_ingress"}
            for name in meta
        ):
            return None
        text = row.get("content", "")
        if (
            not verified_ingress(ingress)
            or principal_from_record(ingress.get("principal")) != actor
            or log._canonical_key(ingress.get("source_thread", ""))
            != log._canonical_key(key)
            or hashlib.sha256(text.encode()).hexdigest() != ingress.get("source_digest")
        ):
            return None
        return event, ingress, actor
    return None


def owner_word_capture(
    log, session_key: str, row: dict | None
) -> VerifiedCapture | None:
    from dataclasses import replace

    from gideon.security.session_signing import load_or_create_key
    from gideon.security.session_credentials import current_work

    work = current_work()
    if log is None or row is None or work is None or work.memory_mode != "persistent":
        return None
    effective = work.work_actor or work.initiator
    if work.initiator.kind != OWNER or effective.kind != OWNER:
        return None
    key = log._canonical_key(session_key)
    if log._canonical_key(work.origin_session_key) != key:
        return None
    ingress_id = row.get("meta", {}).get("ingress", {}).get("source_event_id")
    if work.ingress_event_id != ingress_id:
        return None
    source = _source(log, key, ingress_id)
    if source is None:
        return None
    event, ingress, actor = source
    if actor != work.initiator or work.ingress_digest != ingress["source_digest"]:
        return None
    # Mixed contributors in the execution stay excluded until an authenticated projection exists.
    for candidate in log.source_events(key):
        meta = candidate.message.get("meta") or {}
        if candidate.message.get("role") == "user" and meta.get("turn_id") == row.get(
            "meta", {}
        ).get("turn_id"):
            other = meta.get("ingress") or {}
            from gideon.security.durable_work import verified_ingress

            if (
                not verified_ingress(other)
                or principal_from_record(other.get("principal")) != actor
                or meta.get("merged_ingress")
            ):
                return None
    capture = VerifiedCapture(
        actor,
        effective,
        key,
        ingress_id,
        ingress["source_digest"],
        event.message["content"],
        session_id_for_key(key),
        Id(event.source_event_id),
        Digest(event.source_digest),
        event.raw_bytes,
        executing_turn_id=work.turn_id,
    )
    signature = hmac.new(
        load_or_create_key(), _DOMAIN + _payload(capture), hashlib.sha256
    ).hexdigest()
    return replace(capture, signature=signature)


def validate_capture(capture) -> VerifiedCapture | None:
    from gideon.cognition.history import ConversationLog
    from gideon.core.config.loader import config_dir
    from gideon.security.session_signing import KEY_BYTES, key_path
    from gideon.security.session_credentials import current_work

    if (
        not isinstance(capture, VerifiedCapture)
        or capture.capture_kind != "owner_words"
    ):
        return None
    work = current_work()
    if (
        work is None
        or work.memory_mode != "persistent"
        or work.turn_id != capture.executing_turn_id
        or work.initiator != capture.original_actor
        or (work.work_actor or work.initiator) != capture.effective_work_actor
        or capture.original_actor.kind != OWNER
        or capture.effective_work_actor.kind != OWNER
        or work.ingress_event_id != capture.ingress_event_id
        or work.ingress_digest != capture.ingress_own_digest
    ):
        return None
    try:
        key = key_path().read_bytes()
        if len(key) < KEY_BYTES or not hmac.compare_digest(
            hmac.new(key, _DOMAIN + _payload(capture), hashlib.sha256).hexdigest(),
            capture.signature,
        ):
            return None
        log = ConversationLog(base_dir=config_dir() / "sessions")
        source = _source(log, capture.origin_session_key, capture.ingress_event_id)
        if source is None:
            return None
        event, ingress, actor = source
        for candidate in log.source_events(capture.origin_session_key):
            meta = candidate.message.get("meta") or {}
            if (
                candidate.message.get("role") == "user"
                and meta.get("turn_id") == capture.executing_turn_id
            ):
                from gideon.security.durable_work import verified_ingress

                other = meta.get("ingress") or {}
                if (
                    not verified_ingress(other)
                    or principal_from_record(other.get("principal"))
                    != capture.original_actor
                    or meta.get("merged_ingress")
                ):
                    return None
        if (
            event.source_event_id != capture.native_source_event_id
            or event.source_digest != capture.native_source_digest
            or event.raw_bytes != capture.source_bytes
            or actor != capture.original_actor
            or event.message["content"] != capture.own_text
            or ingress["source_digest"] != capture.ingress_own_digest
            or session_id_for_key(capture.origin_session_key)
            != capture.history_session_id
        ):
            return None
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return capture


def current_capture() -> VerifiedCapture | None:
    return validate_capture(_CURRENT.get())


@contextmanager
def capturing(capture):
    token = _CURRENT.set(validate_capture(capture))
    try:
        yield
    finally:
        _CURRENT.reset(token)

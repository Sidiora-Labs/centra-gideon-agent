"""Host-attested native tool results; never evidence of human-authored words."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json

from gideon.security.approval_answer import OWNER, Principal, principal_from_record, principal_record

_RESULT_DOMAIN = b'gideon.native-tool-result.v1\0'
_PROJECTION_DOMAIN = b'gideon.native-tool-projection.v1\0'


def _payload(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _sign(value, domain):
    from gideon.interfaces.dashboard.session_store import load_or_create_key
    return dict(value, signature=hmac.new(load_or_create_key(), domain + _payload(value), hashlib.sha256).hexdigest())


def _verify(value, domain):
    from gideon.interfaces.dashboard.session_store import key_path, KEY_BYTES
    if not isinstance(value, dict):
        return None
    unsigned = {k: v for k, v in value.items() if k != 'signature'}
    try:
        key = key_path().read_bytes()
        if len(key) < KEY_BYTES or not hmac.compare_digest(
                hmac.new(key, domain + _payload(unsigned), hashlib.sha256).hexdigest(), value.get('signature', '')):
            return None
    except (OSError, TypeError, ValueError):
        return None
    return unsigned


def native_result_origin(runtime, prepared, result: str, metadata: dict) -> dict | None:
    """Called only by the actual native result producer, after its result is final."""
    from gideon.engine.agents.native.runtime import NativeAgentRuntime, _PreparedCall
    from gideon.security.session_credentials import current_work
    from gideon.security.security import is_denial_observation
    work = current_work()
    if (not isinstance(runtime, NativeAgentRuntime) or not isinstance(prepared, _PreparedCall)
            or work is None or runtime._session_key != work.session_key
            or prepared.tool_name not in runtime._tool_index or not prepared.call.tool_call_id
            or work.memory_mode != 'persistent' or work.initiator.kind != OWNER
            or work.created_by_app
            or (work.work_actor or work.initiator).kind != OWNER
            or not work.ingress_event_id or not work.ingress_digest
            or not isinstance(result, str) or not isinstance(metadata.get('ok'), bool)):
        return None
    status = 'success' if metadata['ok'] else 'denied' if is_denial_observation(result) else 'failed'
    return _sign({'kind': 'tool_outcome', 'version': 1, 'session_key': work.session_key,
                  'origin_session_key': work.origin_session_key, 'turn_id': work.turn_id,
                  'original_actor': principal_record(work.initiator),
                  'effective_actor': principal_record(work.work_actor or work.initiator),
                  'ingress_event_id': work.ingress_event_id, 'ingress_digest': work.ingress_digest,
                  'call_id': prepared.call.tool_call_id, 'tool_name': prepared.tool_name,
                  'outcome': status, 'result_digest': hashlib.sha256(result.encode()).hexdigest()}, _RESULT_DOMAIN)


def project_native_result(origin, *, call_id: str, tool_name: str, result: str, projected: str) -> dict | None:
    """Bind the server's canonical result projection to the actual native result."""
    from gideon.security.session_credentials import current_work
    values = _verify(origin, _RESULT_DOMAIN)
    work = current_work()
    if (values is None or work is None or values.get('kind') != 'tool_outcome'
            or values.get('session_key') != work.session_key or values.get('turn_id') != work.turn_id
            or values.get('call_id') != call_id or values.get('tool_name') != tool_name
            or values.get('result_digest') != hashlib.sha256(result.encode()).hexdigest()
            or values.get('original_actor') != principal_record(work.initiator)
            or values.get('effective_actor') != principal_record(work.work_actor or work.initiator)
            or values.get('ingress_event_id') != work.ingress_event_id
            or values.get('ingress_digest') != work.ingress_digest):
        return None
    return _sign({'result_origin': origin, 'projected_digest': hashlib.sha256(projected.encode()).hexdigest()}, _PROJECTION_DOMAIN)


@dataclass(frozen=True)
class ToolContributor:
    original_actor: Principal
    ingress_event_id: str
    ingress_digest: str
    native_source_event_id: str
    native_source_digest: str
    source_bytes: bytes = field(repr=False)


@dataclass(frozen=True)
class VerifiedToolOutcome:
    """Authenticated tool-result source, distinct from human-authored input."""
    session_key: str
    turn_id: str
    call_id: str
    tool_name: str
    outcome: str
    original_actor: Principal
    effective_actor: Principal
    result_digest: str
    projected_digest: str
    native_source_event_id: str
    native_source_digest: str
    source_bytes: bytes = field(repr=False)
    contributors: tuple[ToolContributor, ...]
    projection: str = field(repr=False)


def verified_turn_outcomes(log, session_key: str) -> tuple[VerifiedToolOutcome, ...]:
    """Read only authenticated same-turn tool status with every contributor verified."""
    from gideon.security.session_credentials import current_work
    from gideon.security.durable_work import verified_ingress
    work = current_work()
    if (work is None or work.memory_mode != 'persistent' or work.initiator.kind != OWNER
            or work.created_by_app
            or (work.work_actor or work.initiator).kind != OWNER
            or log._canonical_key(session_key) != log._canonical_key(work.origin_session_key)):
        return ()
    key = log._canonical_key(session_key)
    metadata = log.get_metadata(key)
    if (metadata.get('memory_mode') != 'persistent' or metadata.get('closed')
            or metadata.get('lifecycle', 'active') != 'active'
            or principal_from_record(metadata.get('initiator')) != work.initiator
            or metadata.get('created_by_app')):
        return ()
    events = tuple(log.source_events(key))
    contributors = []
    for event in events:
        row = event.message
        meta = row.get('meta') or {}
        if row.get('role') != 'user' or meta.get('turn_id') != work.turn_id:
            continue
        ingress = meta.get('ingress') or {}
        if (not verified_ingress(ingress) or principal_from_record(ingress.get('principal')) != work.initiator
                or any('paste' in name or name in {'merged_ingress', 'injected', 'replay_ingress'} for name in meta)
                or log._canonical_key(ingress.get('source_thread', '')) != key
                or not isinstance(row.get('content'), str)
                or hashlib.sha256(row['content'].encode()).hexdigest() != ingress.get('source_digest')):
            return ()
        contributors.append(ToolContributor(work.initiator, ingress['source_event_id'], ingress['source_digest'],
                                             event.source_event_id, event.source_digest, event.raw_bytes))
    if not any(c.ingress_event_id == work.ingress_event_id and c.ingress_digest == work.ingress_digest for c in contributors):
        return ()
    outcomes = []
    seen = set()
    for event in events:
        row = event.message
        meta = row.get('meta') or {}
        if row.get('role') != 'tool' or meta.get('turn_id') != work.turn_id or meta.get('done') is not True:
            continue
        projection = meta.get('native_outcome_origin')
        projected = _verify(projection, _PROJECTION_DOMAIN)
        values = _verify(projected.get('result_origin'), _RESULT_DOMAIN) if projected is not None else None
        if (values is None or values.get('kind') != 'tool_outcome' or values.get('version') != 1
                or values.get('session_key') != work.session_key or values.get('turn_id') != work.turn_id
                or values.get('original_actor') != principal_record(work.initiator)
                or values.get('effective_actor') != principal_record(work.work_actor or work.initiator)
                or values.get('ingress_event_id') != work.ingress_event_id or values.get('ingress_digest') != work.ingress_digest
                or values.get('call_id') != meta.get('tool_call_id') or values.get('tool_name') != row.get('content')
                or values.get('outcome') not in {'success', 'failed', 'denied'}
                or not isinstance(meta.get('output'), str)
                or hashlib.sha256(meta['output'].encode()).hexdigest() != projected.get('projected_digest')):
            return ()  # A partial contributor/result set cannot teach the whole turn.
        if values['call_id'] in seen:
            return ()
        seen.add(values['call_id'])
        outcomes.append(VerifiedToolOutcome(work.session_key, work.turn_id, values['call_id'], values['tool_name'],
            values['outcome'], work.initiator, work.work_actor or work.initiator, values['result_digest'],
            projected['projected_digest'], event.source_event_id, event.source_digest, event.raw_bytes,
            tuple(contributors), _payload(projection).decode()))
    return tuple(outcomes)


def validate_tool_outcome(outcome) -> VerifiedToolOutcome | None:
    from gideon.cognition.history import ConversationLog
    from gideon.core.config.loader import config_dir
    if not isinstance(outcome, VerifiedToolOutcome):
        return None
    try:
        log = ConversationLog(base_dir=config_dir() / 'sessions')
        return next((candidate for candidate in verified_turn_outcomes(log, outcome.session_key)
                     if candidate == outcome), None)
    except (OSError, KeyError, TypeError, ValueError):
        return None

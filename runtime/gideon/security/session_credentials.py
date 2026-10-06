"""Ephemeral, turn-bound internal work proof; never an owner login or approval grant."""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from gideon.security.approval_answer import Principal, UNKNOWN


@dataclass(frozen=True)
class BoundWork:
    session_key: str
    origin_session_key: str
    turn_id: str
    initiator: Principal
    created_by_app: str
    memory_mode: str
    expires_at: float
    work_actor: Principal | None = None
    durable_run_id: str = ''
    ingress_event_id: str = ''
    ingress_digest: str = ''
    durable_event_id: str = ''
    execution_model: str = ''
    execution_runtime: str = ''
    allowed_models: tuple[str, ...] = ()
    trigger_origin: object = field(default=None, repr=False)


@dataclass
class TurnCredential:
    work: BoundWork
    bearer: str = field(repr=False)
    context: object = field(repr=False)
    pid_files: dict[Path, str] = field(default_factory=dict, repr=False)


_active: dict[str, TurnCredential] = {}
_by_session: dict[str, str] = {}
_current: contextvars.ContextVar[BoundWork | None] = contextvars.ContextVar('bound_work', default=None)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def current_work() -> BoundWork | None:
    work = _current.get()
    if work is None:
        return None
    credential = _active.get(_by_session.get(work.session_key, ''))
    return work if credential is not None and verify(credential.bearer, work.session_key) is work else None


@dataclass(frozen=True)
class MemoryReach:
    """Provenance limits intersect native authority; this is not a capability."""
    scope: object
    read_allowed: bool
    write_allowed: bool
    background_allowed: bool
    reason: str = ''


def memory_reach(scope=None, *, require_bound: bool = True, app_receipt=None) -> MemoryReach:
    from gideon.security.approval_answer import APP, OWNER
    work = current_work()
    if work is None:
        # Administrative native clients still use their typed capability. A stale
        # host context or model dispatcher cannot fall back to that allowance.
        from gideon.integrations.mcp_core import _CURRENT_SESSION_KEY
        administrative = not require_bound and _current.get() is None and not _CURRENT_SESSION_KEY.get()
        return MemoryReach(scope, administrative, administrative, administrative,
                           '' if administrative else 'unverified_work')
    actor = work.work_actor or work.initiator
    if work.trigger_origin is not None and not admitted_memory_tool(work):
        return MemoryReach(scope, False, False, False, 'foreign_origin')
    if actor.kind == APP:
        from gideon.extensions.apps.app_work import from_bound
        from gideon.extensions.apps.permissions import checker_for
        app_work = from_bound(work)
        checker = checker_for(actor.name)
        if app_work is None or app_work.current_tier() in {'', 'text'}:
            return MemoryReach(scope, False, False, False, 'app_task_only')
        if checker is None:
            return MemoryReach(scope, False, False, False, 'app_memory_not_granted')
        if not checker.can_use_memory('shared'):
            from gideon.hypermid.contracts import AppScopeReceipt
            from gideon.hypermid.app_scopes import _installed_app
            if not checker.can_use_memory('app-scoped') or not isinstance(app_receipt, AppScopeReceipt) or app_receipt.revoked or app_receipt.app_name != app_work.app or scope != app_receipt.scope:
                return MemoryReach(scope, False, False, False, 'app_memory_scope_unverified')
            try:
                if app_receipt.manifest_digest != _installed_app(app_work.app):
                    return MemoryReach(scope, False, False, False, 'app_memory_consent_changed')
            except Exception:
                return MemoryReach(scope, False, False, False, 'app_memory_consent_unavailable')
    elif work.initiator.kind != OWNER:
        tool = admitted_memory_tool(work)
        if not tool or work.memory_mode == 'temporary':
            return MemoryReach(scope, False, False, False, 'foreign_origin')
        reads = tool in {'memory_recall', 'memory_list', 'memory_forget'}
        writes = tool in {'memory_remember', 'memory_forget'} and work.memory_mode == 'persistent'
        return MemoryReach(scope, reads, writes, False, '')
    reads = work.memory_mode != 'temporary'
    writes = work.memory_mode == 'persistent'
    return MemoryReach(scope, reads, writes, writes and work.initiator.kind == OWNER, '' if reads else 'temporary_work')


def begin_turn(session_key: str, initiator: Principal, *, turn_id: str,
               created_by_app: str = '', memory_mode: str = 'temporary',
               origin_session_key: str = '', ttl: float = 7200, work_actor: Principal | None = None,
               durable_run_id: str = '', ingress_event_id: str = '', ingress_digest: str = '',
               durable_event_id: str = '', execution_model: str = '',
               execution_runtime: str = '', allowed_models: tuple[str, ...] = (),
               trigger_origin=None) -> TurnCredential | None:
    if not session_key or initiator.kind == UNKNOWN or memory_mode not in {'persistent','incognito','temporary'}:
        return None
    work = BoundWork(session_key, origin_session_key or session_key, turn_id, initiator,
                     created_by_app, memory_mode, time.monotonic()+max(0,ttl), work_actor, durable_run_id,
                     ingress_event_id, ingress_digest, durable_event_id,
                     execution_model, execution_runtime, tuple(allowed_models), trigger_origin)
    bearer = "gwsp_"+secrets.token_urlsafe(32)
    credential = TurnCredential(work,bearer,_current.set(work))
    old = _by_session.get(session_key)
    if old and old in _active:
        _discard(_active[old])
    key = _digest(bearer)
    _active[key] = credential
    _by_session[session_key] = key
    return credential


def verify(bearer: str, session_key: str) -> BoundWork | None:
    credential = _active.get(_digest(bearer)) if bearer else None
    if credential is None or credential.work.session_key != session_key:
        return None
    if credential.work.expires_at <= time.monotonic():
        _discard(credential)
        return None
    if credential.work.trigger_origin is not None:
        from gideon.security.durable_work import trigger_proof_valid
        if not trigger_proof_valid(credential.work):
            _discard(credential)
            return None
    if credential.work.durable_run_id:
        from gideon.security.durable_work import workflow_proof_valid
        if not workflow_proof_valid(credential.work):
            _discard(credential)
            return None
    return credential.work


def credential_for(session_key: str) -> str:
    credential = _active.get(_by_session.get(session_key,''))
    return credential.bearer if credential and verify(credential.bearer,session_key) else ''


def _discard(credential: TurnCredential) -> None:
    key = _digest(credential.bearer)
    _active.pop(key,None)
    if _by_session.get(credential.work.session_key) == key:
        _by_session.pop(credential.work.session_key,None)
    for path,nonce in credential.pid_files.items():
        try:
            if json.loads(path.read_text()).get('nonce') == nonce:
                path.unlink(missing_ok=True)
        except (OSError,ValueError,AttributeError):
            pass


def end_turn(credential: TurnCredential | None) -> None:
    if credential is not None:
        _discard(credential)
        _current.reset(credential.context)


def process_start(pid: int) -> int | None:
    from gideon.integrations.acp.transport import _get_start_time
    return _get_start_time(pid)


def _proof_path(pid: int) -> Path:
    from gideon.core.config.loader import config_dir
    # Both durability inventory and portability ignore the existing tmp tree.
    return config_dir()/'tmp'/'session-proofs'/f'pid-{pid}.json'


def publish_pid(session_key: str, pid: int) -> None:
    bearer = credential_for(session_key)
    credential = _active.get(_digest(bearer)) if bearer else None
    started = process_start(pid)
    if credential is None or started is None:
        return
    path = _proof_path(pid)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    nonce = secrets.token_hex(16)
    record = {'session_key':session_key,'proof':bearer,'process_start':started,'nonce':nonce}
    from gideon.core.atomic_write import atomic_write
    atomic_write(path,json.dumps(record),mode=0o600,fsync=True)
    credential.pid_files[path] = nonce


def forget_pid(pid: int) -> None:
    path = _proof_path(pid)
    for credential in tuple(_active.values()):
        if path in credential.pid_files:
            nonce = credential.pid_files.pop(path)
            try:
                if json.loads(path.read_text()).get('nonce') == nonce:
                    path.unlink(missing_ok=True)
            except (OSError,ValueError,AttributeError):
                pass


def inherited_binding() -> dict | None:
    """Read a live process-start-matched parent binding before stale environment."""
    from gideon.integrations.mcp_core import _get_ppid
    pid,seen = os.getppid(),set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            record=json.loads(_proof_path(pid).read_text())
            if record.get('process_start') == process_start(pid):
                return record
        except (OSError,ValueError,AttributeError):
            pass
        pid=_get_ppid(pid)
    return None


def inherited_proof(session_key: str) -> str:
    binding = inherited_binding()
    if binding is not None:
        return str(binding.get('proof','')) if binding.get('session_key') == session_key else ''
    return os.environ.get('GIDEON_SESSION_PROOF','') if os.environ.get('GIDEON_SESSION_KEY','') == session_key else ''


def begin_child_turn(session_key: str, parent: BoundWork | None, *, turn_id: str) -> TurnCredential | None:
    if parent is None:
        return None
    return begin_turn(session_key,parent.initiator,turn_id=turn_id,
                      origin_session_key=parent.origin_session_key,
                      created_by_app=parent.created_by_app,memory_mode=parent.memory_mode,
                      ttl=max(0,parent.expires_at-time.monotonic()),work_actor=parent.work_actor,
                      durable_run_id=parent.durable_run_id, ingress_event_id=parent.ingress_event_id,
                      ingress_digest=parent.ingress_digest, durable_event_id=parent.durable_event_id,
                      execution_model=parent.execution_model, execution_runtime=parent.execution_runtime,
                      allowed_models=parent.allowed_models, trigger_origin=parent.trigger_origin)


def work_of_request(request) -> BoundWork | None:
    value=request.get('_session_work_proof')
    if not isinstance(value,BoundWork):
        return None
    live=verify(request.headers.get('X-Session-Proof',''),request.headers.get('X-Session-Key',''))
    return value if live is value else None


def bind_execution(credential: TurnCredential, runtime) -> BoundWork:
    """Bind the first actual host-acquired runtime, never a caller model label."""
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.integrations.llm.acp_agent import AcpAgentProvider
    from gideon.integrations.llm.acp_session_provider import AcpSessionProvider
    if not isinstance(credential, TurnCredential) or verify(credential.bearer, credential.work.session_key) is not credential.work:
        raise PermissionError('execution binding requires the active host credential')
    if isinstance(runtime, NativeAgentRuntime):
        identity = 'native'
        model = str(getattr(runtime._model, 'served_model_ref', '') or runtime._preferred_model_ref or '')
    elif isinstance(runtime, (AcpAgentProvider, AcpSessionProvider)):
        identity = runtime.provider_id
        model = identity
        if not identity.startswith('acp:'):
            raise PermissionError('acquired ACP runtime identity is invalid')
    else:
        raise PermissionError('execution binding requires an actual native or ACP runtime')
    if not model:
        raise PermissionError('acquired runtime has no served model identity')
    work = credential.work
    if work.execution_model:
        if work.memory_mode in {'temporary', 'incognito'} and (work.execution_model, work.execution_runtime, work.allowed_models) != (model, identity, (model,)):
            raise PermissionError('acquired runtime differs from the authenticated execution origin')
        return work
    if work.durable_run_id:
        if work.memory_mode == 'persistent':
            return work
        raise PermissionError('durable work cannot replace a missing original execution envelope')
    updated = replace(work, execution_model=model, execution_runtime=identity, allowed_models=(model,))
    credential.work = updated
    if _current.get() is work:
        _current.set(updated)
    return updated


_MEMORY_OPERATION = contextvars.ContextVar('bound_memory_operation', default=None)


def admitted_memory_tool(work=None) -> str:
    work = work or current_work()
    admission = _MEMORY_OPERATION.get()
    if work is None or admission is None or admission[0] is not work:
        return ''
    from gideon.security.durable_work import declared_memory_tool
    return admission[1] if declared_memory_tool(work, admission[1]) else ''


def memory_tool_endpoint(tool: str):
    """Fixed server endpoint admission; request arguments cannot select authority."""
    if tool not in {'memory_recall', 'memory_list', 'memory_remember', 'memory_forget'}:
        raise ValueError('unknown native memory tool')
    import functools
    def decorate(handler):
        @functools.wraps(handler)
        async def admitted(request):
            proof = work_of_request(request)
            current_token = _current.set(proof) if proof is not None else None
            operation_token = _MEMORY_OPERATION.set((proof, tool)) if proof is not None else None
            try:
                if proof is not None and (proof.initiator.kind != 'owner' or (proof.work_actor or proof.initiator).kind == 'app'):
                    from aiohttp import web
                    app_work = (proof.work_actor or proof.initiator).kind == 'app'
                    if not app_work and not admitted_memory_tool(proof):
                        return web.json_response({'error': 'This memory operation is not included in the current owner-approved trigger grant.'}, status=403)
                    # Native authority must already be installed; never create a legacy store.
                    state = request.app['state']
                    from gideon.hypermid.memory import HypermidMemoryProvider
                    builder = getattr(state, 'context_builder', None)
                    memory = getattr(builder, 'memory', None)
                    records = getattr(memory, 'vector_store', None)
                    if not isinstance(records, HypermidMemoryProvider):
                        return web.json_response({'error': 'Native scoped memory authority is unavailable.'}, status=503)
                try:
                    return await handler(request)
                except Exception as error:
                    from gideon.hypermid.client import HypermidRemoteError
                    if isinstance(error, HypermidRemoteError) and error.error.code == 'AUTHORIZATION_DENIED':
                        from aiohttp import web
                        return web.json_response({'error': 'Native memory authorization refused this operation in the current scope.'}, status=403)
                    raise
            finally:
                if operation_token is not None:
                    _MEMORY_OPERATION.reset(operation_token)
                if current_token is not None:
                    _current.reset(current_token)
        return admitted
    return decorate

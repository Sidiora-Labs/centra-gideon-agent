"""Workflow execution receipts issued and retired by the native private-scope authority."""
from __future__ import annotations
import asyncio
import inspect
from contextvars import ContextVar
from contextlib import contextmanager
from gideon.hypermid.memory import HypermidMemoryProvider, _trace
from gideon.hypermid.private_scopes import NativePrivateScopes
from gideon.security.session_credentials import BoundWork, credential_for, verify

ORIGIN_KEY = 'private_scope_origin'
SCOPE_KEY = 'private_scope_id'
_FORK_ADMISSION = ContextVar('private_workflow_fork_admission', default=None)


@contextmanager
def admitted_fork(parent_id, origin):
    token = _FORK_ADMISSION.set((parent_id, origin))
    try:
        yield
    finally:
        _FORK_ADMISSION.reset(token)


def fork_origin(parent_id):
    admitted = _FORK_ADMISSION.get()
    return admitted[1] if admitted is not None and admitted[0] == parent_id else None


def provider_for(supervisor):
    memory = getattr(getattr(supervisor, '_services', None), 'memory', None)
    if memory is None:
        memory = getattr(getattr(getattr(supervisor, '_state', None), 'context_builder', None), 'memory', None)
    provider = memory if isinstance(memory, HypermidMemoryProvider) else getattr(memory, 'provider', None)
    if not isinstance(provider, HypermidMemoryProvider):
        raise RuntimeError('native private execution scope authority is unavailable')
    return provider


def receipts(supervisor):
    if supervisor is None:
        raise RuntimeError('private execution requires a live supervisor')
    result = getattr(supervisor, '_private_receipts', None)
    if result is None:
        result = {}
        supervisor._private_receipts = result
    return result


async def ensure(supervisor, proof):
    if not isinstance(proof, BoundWork) or verify(credential_for(proof.session_key), proof.session_key) is not proof:
        raise RuntimeError('private execution requires verified live origin work')
    if proof.memory_mode not in {'temporary', 'incognito'} or not proof.origin_session_key:
        raise RuntimeError('private origin mode is unavailable')
    provider = provider_for(supervisor)
    known = receipts(supervisor)
    receipt = known.get(proof.origin_session_key)
    if receipt is None:
        receipt = await asyncio.to_thread(provider._loop.call, lambda client: NativePrivateScopes(client).issue(
            origin_session_key=proof.origin_session_key, original_actor=proof.initiator.label,
            memory_mode=proof.memory_mode, ttl_ms=86_400_000, trace=_trace()))
        known[proof.origin_session_key] = receipt
    if receipt.memory_mode != proof.memory_mode or receipt.original_actor != proof.initiator.label:
        raise RuntimeError('private execution receipt does not match the proven origin')
    return await asyncio.to_thread(provider._loop.call, lambda client: NativePrivateScopes(client).resolve(receipt, trace=_trace()))


async def validate_origin(supervisor, origin, *, memory_mode, original_actor):
    """Resolve existing authority for already-admitted work; never issue from metadata."""
    receipt = receipts(supervisor).get(origin)
    if receipt is None or receipt.memory_mode != memory_mode or receipt.original_actor != original_actor:
        raise RuntimeError('private work has no matching live native receipt')
    provider = provider_for(supervisor)
    return await asyncio.to_thread(provider._loop.call, lambda client: NativePrivateScopes(client).resolve(receipt, trace=_trace()))


async def validate_run(supervisor, run):
    from gideon.automation.workflows import ownership
    mode = ownership.run_mode(run)
    if mode is ownership.MemoryMode.NORMAL:
        return None
    origin = str(run.extra.get(ORIGIN_KEY) or '')
    receipt = receipts(supervisor).get(origin)
    if receipt is None or receipt.memory_mode != mode.value or run.extra.get(SCOPE_KEY) != str(receipt.scope_id) or run.extra.get('chat_owner') != origin:
        raise RuntimeError('private run has no live native execution receipt')
    provider = provider_for(supervisor)
    return await asyncio.to_thread(provider._loop.call, lambda client: NativePrivateScopes(client).resolve(receipt, trace=_trace()))


async def retire(supervisor, origin):
    try:
        from gideon.automation.workflows import batch_start
    except ImportError:
        batch_start = None
    end = getattr(batch_start, 'end_private', None)
    if callable(end):
        result = end(origin)
        if inspect.isawaitable(result):
            await result
    provider = provider_for(supervisor)
    result = await asyncio.to_thread(provider._loop.call, lambda client: NativePrivateScopes(client).retire(origin, trace=_trace()))
    if result is None or not result.retired:
        raise RuntimeError('native private scope did not retire')
    receipts(supervisor).pop(origin, None)


async def restore_wait_receipt(supervisor, wire, *, origin, memory_mode, original_actor):
    """Resolve a sealed pending wait's existing native receipt, without issuing a scope."""
    from gideon.hypermid.contracts import PrivateScopeReceipt
    receipt = PrivateScopeReceipt.from_wire(wire)
    if receipt.origin_session_key != origin or receipt.memory_mode != memory_mode or receipt.original_actor != original_actor:
        raise RuntimeError('pending private receipt does not match its authenticated origin')
    provider = provider_for(supervisor)
    resolved = await asyncio.to_thread(provider._loop.call, lambda client: NativePrivateScopes(client).resolve(receipt, trace=_trace()))
    receipts(supervisor)[origin] = resolved
    return resolved


def wait_receipt_record(receipt):
    from gideon.hypermid.contracts import PrivateScopeReceipt
    if not isinstance(receipt, PrivateScopeReceipt):
        raise TypeError('a typed native private receipt is required')
    return {
        'scope_id': str(receipt.scope_id), 'actor_scope': receipt.actor_scope.to_wire(),
        'scope': receipt.scope.to_wire(), 'capability_id': str(receipt.capability_id),
        'origin_session_key': str(receipt.origin_session_key), 'original_actor': str(receipt.original_actor),
        'memory_mode': receipt.memory_mode, 'expires_at_ms': receipt.expires_at_ms, 'retired': receipt.retired}

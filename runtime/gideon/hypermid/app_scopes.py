"""Native app namespaces issued from live host-bound app work."""
from __future__ import annotations

import json
from .contracts import AppScopeReceipt, MemoryContractError
from .foundation import Digest, Trace
from .memory_client import MemoryClient


def _installed_app(app: str) -> Digest:
    from gideon.extensions.apps.app_manager import _manifest_of
    from gideon.extensions.apps.manager import _read_installed
    from gideon.extensions.apps.permissions import agent_tier_now, checker_for
    installed, manifest = _read_installed(app), _manifest_of(app)
    checker = checker_for(app)
    if installed is None or installed.name != app or not installed.enabled or manifest is None or manifest.name != app or checker is None or not checker.can_use_memory('app-scoped') or agent_tier_now(app) not in {'read','tools'}:
        raise MemoryContractError('installed app memory consent is unavailable')
    return Digest.sha256(json.dumps(manifest.to_dict(),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())


def _bound_app(*, issuing: bool = False) -> tuple[str, Digest]:
    from gideon.extensions.apps.app_work import from_bound
    from gideon.security.session_credentials import current_work
    work = current_work()
    app = from_bound(work)
    if work is None or work.memory_mode not in ({'persistent'} if issuing else {'persistent','incognito'}) or app is None or app.current_tier() not in {'read','tools'}:
        raise MemoryContractError('persistent app memory requires verified live app work')
    return app.app, _installed_app(app.app)


class NativeAppScopes:
    def __init__(self, client: MemoryClient) -> None:
        self.client = client

    async def issue(self, *, trace: Trace, ttl_ms: int = 86_400_000) -> AppScopeReceipt:
        app, digest = _bound_app(issuing=True)
        if type(ttl_ms) is not int or not 0 < ttl_ms <= 2_592_000_000:
            raise MemoryContractError('app namespace lifetime is invalid')
        value = await self.client._call('memory.app-scope.issue', {'app_name':app,'manifest_digest':str(digest),'ttl_ms':ttl_ms},trace=trace,durable=True)
        result = AppScopeReceipt.from_wire(value)
        try:
            current_app, current_digest = _bound_app(issuing=True)
            if result.actor_scope != self.client.scope or result.app_name != current_app or result.manifest_digest != current_digest or result.revoked:
                raise MemoryContractError('app changed while its namespace was issued')
        except Exception:
            await self.client._call('memory.app-scope.revoke', {'app_name':app,'expected_capability_id':str(result.capability_id)},trace=trace,durable=True)
            raise
        return result

    async def lookup(self, *, trace: Trace) -> AppScopeReceipt | None:
        app, digest = _bound_app()
        value = await self.client._call('memory.app-scope.lookup', {'app_name':app},trace=trace)
        raw = value.get('receipt')
        if raw is None:
            return None
        result = AppScopeReceipt.from_wire(raw)
        if result.actor_scope != self.client.scope or result.app_name != app or result.manifest_digest != digest or result.revoked:
            raise MemoryContractError('app namespace lookup does not match live consent')
        return await self.resolve(result,trace=trace)

    async def activate_installed(self, app: str, *, trace: Trace) -> AppScopeReceipt:
        from gideon.security.session_credentials import current_work
        from gideon.security.approval_answer import OWNER
        work = current_work()
        if work is not None and (work.initiator.kind != OWNER or work.work_actor is not None or work.created_by_app):
            raise MemoryContractError('app memory activation requires owner review')
        digest = _installed_app(app)
        raw = await self.client._call('memory.app-scope.issue', {'app_name':app,'manifest_digest':str(digest),'ttl_ms':86_400_000},trace=trace,durable=True)
        result = AppScopeReceipt.from_wire(raw)
        if result.actor_scope != self.client.scope or result.app_name != app or result.manifest_digest != _installed_app(app) or result.revoked:
            raise MemoryContractError('app changed during memory activation')
        return result

    async def resolve(self, receipt: AppScopeReceipt, *, trace: Trace) -> AppScopeReceipt:
        app, digest = _bound_app()
        if not isinstance(receipt, AppScopeReceipt) or receipt.actor_scope != self.client.scope or receipt.app_name != app or receipt.manifest_digest != digest or receipt.revoked:
            raise MemoryContractError('app namespace receipt does not match live work')
        value = await self.client._call('memory.app-scope.resolve', {'scope_id':str(receipt.scope_id),'target_scope':receipt.scope.to_wire(),'capability_id':str(receipt.capability_id)},trace=trace,capability=False)
        result = AppScopeReceipt.from_wire(value)
        if result != receipt or _bound_app() != (app,digest):
            raise MemoryContractError('app namespace activation changed')
        return result

    async def revoke_installed(self, app: str, *, trace: Trace) -> AppScopeReceipt | None:
        # Only owner lifecycle calls supply a catalog name; app work cannot revoke grants.
        from gideon.security.session_credentials import current_work
        from gideon.security.approval_answer import OWNER
        work = current_work()
        if work is not None and (work.initiator.kind != OWNER or work.work_actor is not None or work.created_by_app):
            raise MemoryContractError('app namespace revocation requires the owner lifecycle')
        value = await self.client._call('memory.app-scope.revoke', {'app_name':app},trace=trace,durable=True)
        result = value.get('receipt')
        if result is None:
            return None
        receipt = AppScopeReceipt.from_wire(result)
        if receipt.actor_scope != self.client.scope or receipt.app_name != app or not receipt.revoked:
            raise MemoryContractError('app namespace revocation could not be verified')
        return receipt


def revoke_app_namespace(app: str) -> None:
    """Revoke active native memory grants before an installed app is stopped."""
    from gideon.cognition import memory_service
    from .memory import HypermidMemoryProvider, _trace
    authority = memory_service._authoritative_service
    provider = authority.provider if authority is not None else None
    if provider is None:
        return
    if not isinstance(provider, HypermidMemoryProvider):
        return
    provider._loop.call(lambda client: NativeAppScopes(client).revoke_installed(app,trace=_trace()))


def activate_app_namespace(app: str) -> None:
    from gideon.cognition import memory_service
    from gideon.extensions.apps.app_manager import _manifest_of
    from .memory import HypermidMemoryProvider, _trace
    manifest = _manifest_of(app)
    if manifest is None or not manifest.permissions.memory:
        return
    authority = memory_service._authoritative_service
    provider = authority.provider if authority is not None else None
    if isinstance(provider, HypermidMemoryProvider):
        provider._loop.call(lambda client: NativeAppScopes(client).activate_installed(app,trace=_trace()))

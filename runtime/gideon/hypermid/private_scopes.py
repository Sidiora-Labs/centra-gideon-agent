"""Typed owner-issued private execution scope lifecycle."""
from .contracts import MemoryContractError, PrivateScopeReceipt
from .foundation import Id, Trace
from .memory_client import MemoryClient


class NativePrivateScopes:
    def __init__(self, client: MemoryClient) -> None:
        self.client = client

    async def issue(self, *, origin_session_key: str, original_actor: str, memory_mode: str, ttl_ms: int, trace: Trace) -> PrivateScopeReceipt:
        if memory_mode not in {"temporary", "incognito"} or type(ttl_ms) is not int or not 0 < ttl_ms <= 86_400_000:
            raise MemoryContractError("private execution lifetime is invalid")
        value = await self.client._call("memory.private-scope.issue", {"origin_session_key": str(Id(origin_session_key)), "original_actor": str(Id(original_actor)), "memory_mode": memory_mode, "ttl_ms": ttl_ms}, trace=trace, durable=True)
        receipt = PrivateScopeReceipt.from_wire(value)
        if receipt.actor_scope != self.client.scope or receipt.origin_session_key != origin_session_key or receipt.original_actor != original_actor or receipt.memory_mode != memory_mode or receipt.retired:
            raise MemoryContractError("private scope receipt does not match its origin")
        return receipt

    async def resolve(self, receipt: PrivateScopeReceipt, *, trace: Trace) -> PrivateScopeReceipt:
        if not isinstance(receipt, PrivateScopeReceipt) or receipt.actor_scope != self.client.scope or receipt.retired:
            raise MemoryContractError("private scope receipt is unavailable")
        value = await self.client._call("memory.private-scope.resolve", {"scope_id": str(receipt.scope_id), "target_scope": receipt.scope.to_wire(), "capability_id": str(receipt.capability_id)}, trace=trace, capability=False)
        current = PrivateScopeReceipt.from_wire(value)
        if current != receipt:
            raise MemoryContractError("private scope receipt changed")
        return current

    async def retire(self, origin_session_key: str, *, trace: Trace) -> PrivateScopeReceipt | None:
        value = await self.client._call("memory.private-scope.retire", {"origin_session_key": str(Id(origin_session_key))}, trace=trace, durable=True)
        value = value.get("receipt")
        if value is None:
            return None
        receipt = PrivateScopeReceipt.from_wire(value)
        if receipt.actor_scope != self.client.scope or receipt.origin_session_key != origin_session_key or not receipt.retired:
            raise MemoryContractError("private scope retirement could not be verified")
        return receipt

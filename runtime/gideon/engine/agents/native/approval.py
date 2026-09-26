"""Session-local delivery of native tool permission decisions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
APPROVE = "approve"
REJECT = "reject"
REVISE = "revise"


@dataclass(slots=True)
class _DecisionSlot:
    answer: asyncio.Future[str]

    def deliver(self, value: str) -> bool:
        if self.answer.done():
            return False
        self.answer.set_result(value)
        return True


class ApprovalGate:
    def __init__(self) -> None:
        self._requests: dict[str, _DecisionSlot] = {}

    def register(self, request_id: str) -> asyncio.Future[str]:
        slot = _DecisionSlot(asyncio.get_event_loop().create_future())
        self._requests[request_id] = slot
        return slot.answer

    def resolve(self, request_id: str, decision: str) -> bool:
        entry = self._requests.get(request_id)
        return entry.deliver(decision) if entry is not None else False

    def approve(self, request_id: str) -> bool:
        return self.resolve(request_id, APPROVE)

    def reject(self, request_id: str) -> bool:
        return self.resolve(request_id, REJECT)

    def revise(self, request_id: str) -> bool:
        return self.resolve(request_id, REVISE)

    def cancel_all(self) -> None:
        for slot in tuple(self._requests.values()):
            slot.deliver(REJECT)

    async def request(self, request_id: str, *, timeout: float = 300.0) -> str:
        return await self.wait(request_id, self.register(request_id), timeout=timeout)

    async def wait(
        self, request_id: str, fut: asyncio.Future[str], *, timeout: float = 300.0
    ) -> str:
        try:
            answer = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            logger.info(
                "approval %s timed out after %.0fs → reject", request_id, timeout
            )
            answer = REJECT
        finally:
            self._requests.pop(request_id, None)
        return answer

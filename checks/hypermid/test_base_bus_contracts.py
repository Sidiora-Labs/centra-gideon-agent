from __future__ import annotations

import pytest

from gideon.hypermid.bus import BusMessage, BusViolation, CensusGuard, deterministic_classifications
from gideon.hypermid.foundation import Digest, Id, Scope, Trace


def test_contract_wire_digest_and_neutral_census_disconnect() -> None:
    body = b"stored outside the bus"
    message = BusMessage(
        subject="hm.owner.project.module-events.memory.changed",
        id=Id("event-1"),
        digest=Digest.sha256(body),
        headers={"content-type": "application/json"},
        scope=Scope(Id("owner"), Id("project")),
        trace=Trace(Id("trace-1"), Id("request-1")),
    )
    assert message.to_wire()["digest"] == Digest.sha256(body)
    guard = CensusGuard()
    assert not guard.proven_absent("agent-1")
    guard.snapshot(1, set())
    assert guard.proven_absent("agent-1")
    guard.disconnect()
    assert not guard.proven_absent("agent-1")
    assert all(not item.applicable and item.reason for item in deterministic_classifications().values())
    with pytest.raises(BusViolation):
        BusMessage("", message.id, message.digest, {}, message.scope, message.trace)

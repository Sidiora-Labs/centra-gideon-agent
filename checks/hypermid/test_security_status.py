from __future__ import annotations

import pytest

from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.security_status import SecurityStatusError, create_security_status


@pytest.mark.asyncio
async def test_security_facade_reports_missing_authorities_without_inventing_state() -> None:
    scope = Scope(Id("owner-1"), Id("project-1"), Id("workspace-1"))
    facade = create_security_status(scope)

    assert await facade.status() == {
        "scope": scope.to_wire(),
        "grants": [],
        "decisions": [],
        "availability": "unavailable",
        "error": {
            "code": "SECURITY_STATUS_UNAVAILABLE",
            "message": "network security authority is unavailable",
            "httpStatus": 503,
        },
    }

    for operation in (
        facade.revoke("grant-1"),
        facade.lookup("memory-1"),
        facade.promote("memory-1", 1),
    ):
        with pytest.raises(SecurityStatusError) as raised:
            await operation
        assert raised.value.code == "SECURITY_STATUS_UNAVAILABLE"
        assert raised.value.http_status == 503

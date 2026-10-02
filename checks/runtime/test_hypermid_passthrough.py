from __future__ import annotations

import pytest

from gideon.cognition.context import PromptAssembler
from gideon.cognition.context_engine import DefaultContextEngine
from gideon.cognition.history import ConversationLog
from gideon.cognition.memory import MemoryJournal
from gideon.extensions.skills import ProcedureLibrary
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.models import Scope


def _builder(tmp_path) -> PromptAssembler:
    memory = MemoryJournal(workspace=tmp_path / "memory")
    memory.init()
    history = ConversationLog(tmp_path / "sessions")
    history.init()
    builder = PromptAssembler(
        memory=memory,
        skills=ProcedureLibrary(
            skills_path=tmp_path / "skills", install_builtins=False
        ),
    )
    builder.conversation_log = history
    return builder


@pytest.mark.asyncio
async def test_unavailable_passthrough_preserves_gideon_context_bytes_and_authority(
    tmp_path,
) -> None:
    builder = _builder(tmp_path)
    request = 'Keep exact Unicode: café 界 and JSON {"b":2,"a":1}'
    kwargs = {"session_key": "session-1", "cwd": str(tmp_path)}
    expected = DefaultContextEngine().assemble(
        builder,
        request,
        is_new_session=False,
        **kwargs,
    )
    client = HypermidClient(
        tmp_path / "missing-connection.json",
        scope=Scope("owner-1", "project-1", "workspace-1"),
    )
    adapter = HypermidAdapter(client, mode="pass_through")

    status = await adapter.start()
    actual = adapter.assemble(
        builder,
        request,
        is_new_session=False,
        **kwargs,
    )
    round_trip = await adapter.passthrough(
        {"message": request, "bytes": list(request.encode("utf-8"))}
    )

    assert status.availability == "unavailable"
    assert status.writer == "gideon"
    assert status.scope_bound is False
    assert actual.message.encode("utf-8") == expected.message.encode("utf-8")
    assert actual.hook_result == expected.hook_result
    assert actual.metadata == expected.metadata
    assert round_trip == {
        "message": request,
        "bytes": list(request.encode("utf-8")),
    }


import json
import shlex
import sys
from pathlib import Path

import pytest

from gideon.engine.hooks import (
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_STOP,
    HOOK_EVENT_SUBAGENT_SPAWN,
    ScriptHook,
    ScriptHookStore,
    run_script_hook,
    safe_read_file,
    safe_read_file_bytes,
    validate_file_path,
)


@pytest.fixture
def hook_home(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


def payload_command(tmp_path: Path) -> str:
    script = tmp_path / "receive_payload.py"
    script.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'payload': json.load(sys.stdin), "
        "'event': os.environ.get('EVENT'), 'context': os.environ.get('CONTEXT')}))\n",
        encoding="utf-8",
    )
    return shlex.join([sys.executable, str(script)])


@pytest.mark.asyncio
async def test_scoped_script_receives_attribution_and_persists_run(hook_home, tmp_path):
    store = ScriptHookStore(hook_home)
    hook = store.create(
        {
            "event": HOOK_EVENT_SUBAGENT_SPAWN,
            "matcher": "worker*",
            "provider_config": {"command": payload_command(tmp_path)},
        }
    )
    assert await store.fire_for_ids(hook.event, [], context="worker started") == []
    assert await store.fire_for_ids(hook.event, [hook.id], context="unrelated") == []
    assert hook.run_count == 0
    results = await store.fire_for_ids(
        hook.event,
        [hook.id],
        context="worker started",
        depth=3,
        subagent_id="child-17",
        parent_session_key="session:parent",
        agent_role="researcher",
        tool_input={"query": "owned files"},
        tool_response={"found": 2},
    )
    assert len(results) == 1
    assert results[0].succeeded, results[0]
    received = json.loads(results[0].stdout)
    assert received["event"] == HOOK_EVENT_SUBAGENT_SPAWN
    assert received["context"] == "worker started"
    assert received["payload"] == {
        "hook_event_name": HOOK_EVENT_SUBAGENT_SPAWN,
        "cwd": str(Path.cwd()),
        "__hook_depth": 3,
        "subagent_id": "child-17",
        "parent_session_key": "session:parent",
        "agent_role": "researcher",
        "tool_input": {"query": "owned files"},
        "tool_response": {"found": 2},
    }
    restored = ScriptHookStore(hook_home).get(hook.id)
    assert restored is not None
    assert (restored.run_count, restored.last_status) == (1, "ok")
    assert restored.last_run > 0


@pytest.mark.asyncio
async def test_real_exit_two_records_only_consumable_blocks(hook_home):
    store = ScriptHookStore(hook_home)
    hook = store.create(
        {"event": HOOK_EVENT_PRE_TOOL_USE, "provider_config": {"command": "exit 2"}}
    )
    assert (await store.fire(hook.event))[0].blocked
    assert (hook.run_count, hook.last_status) == (1, "advisory")
    assert (await store.fire_for_ids(hook.event, [hook.id]))[0].blocked
    assert (hook.run_count, hook.last_status) == (2, "blocked")
    store.update(hook.id, {"event": HOOK_EVENT_STOP})
    assert (await store.fire_for_ids(hook.event, [hook.id]))[0].blocked
    restored = ScriptHookStore(hook_home).get(hook.id)
    assert (restored.run_count, restored.last_status) == (3, "advisory")


@pytest.mark.asyncio
async def test_script_rehearsal_copies_payload_without_changing_history(
    hook_home, tmp_path
):
    hook = ScriptHook(
        id="rehearsal",
        provider_config={"command": payload_command(tmp_path)},
        last_run=12.0,
        last_status="queued",
        run_count=7,
    )
    payload = {"hook_event_name": hook.event, "cwd": str(tmp_path), "test": False}
    before = hook.to_dict()
    result = await run_script_hook(hook, "rehearsal input", payload, test=True)
    assert result.succeeded, result
    assert json.loads(result.stdout)["payload"]["test"] is True
    assert payload["test"] is False
    assert hook.to_dict() == before


@pytest.mark.asyncio
async def test_incident_state_prevents_execution_and_recovers(hook_home, tmp_path):
    marker = tmp_path / "ran.txt"
    hook = ScriptHook(
        id="incident-owned",
        provider_config={"command": f"printf ran > {shlex.quote(str(marker))}"},
    )
    incident = hook_home / "incident.json"
    incident.write_text(json.dumps({"active": True, "reason": "owned test"}))
    result = await run_script_hook(hook)
    assert result.error == "skipped: incident mode active"
    assert (hook.run_count, hook.last_status) == (1, "skipped_incident")
    assert not marker.exists()
    before = hook.to_dict()
    result = await run_script_hook(hook, test=True)
    assert result.error == "skipped: incident mode active"
    assert hook.to_dict() == before
    assert not marker.exists()
    incident.unlink()
    result = await run_script_hook(hook)
    assert result.succeeded, result
    assert marker.read_text() == "ran"
    assert (hook.run_count, hook.last_status) == (2, "ok")


@pytest.mark.parametrize(
    "patch",
    [{"event": "unknown"}, {"timeout": 0}, {"timeout": 301}, {"provider_config": []}],
)
def test_invalid_update_preserves_memory_and_disk(hook_home, patch):
    store = ScriptHookStore(hook_home)
    hook = store.create({"name": "original"})
    before = hook.to_dict()
    disk = (hook_home / "hooks.json").read_bytes()
    with pytest.raises(ValueError):
        store.update(hook.id, {"name": "must not change", **patch})
    assert hook.to_dict() == before
    assert (hook_home / "hooks.json").read_bytes() == disk


def test_canonical_file_read_uses_real_target(tmp_path):
    content = tmp_path / "message.txt"
    content.write_text("local content\n", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(content)
    assert validate_file_path(str(alias)) == str(content)
    assert safe_read_file(str(alias)) == "local content\n"
    assert safe_read_file_bytes(str(alias)) == b"local content\n"
    assert validate_file_path("bad\x00path") is None
    with pytest.raises(PermissionError):
        safe_read_file("bad\x00path")

import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.capabilities.identity.continuity import ContinuityStore, heartbeat_turns_paused
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.interfaces.dashboard.handlers.capabilities_identity_continuity import register, PREFIX


def configure(store, **changes):
    return store.configure(**{"heartbeat_paused": True, "expected_revision": 0, "request_id": "pause", **changes})


def test_empty_read_does_not_create_memory_or_report_readiness(tmp_path):
    assert heartbeat_turns_paused(tmp_path) is False
    assert not (tmp_path / "capabilities").exists()
    store = ContinuityStore(tmp_path)
    state = store.status()
    assert state["revision"] == 0
    assert state["heartbeat_paused"] is False
    assert state["slots"] == {"persona": [], "self_notes": []}
    assert state["context"] == ""
    assert state["memory_events"] == []
    assert state["journal"] == []
    assert state["provider_readiness"] == "unknown"
    assert "Scheduled heartbeat turns only" in state["scope"]
    assert not (tmp_path / "memory.db").exists()


def test_pause_resume_replay_order_and_restart(tmp_path):
    store = ContinuityStore(tmp_path)
    first = configure(store)
    assert first == {"revision": 1, "heartbeat_paused": True}
    assert heartbeat_turns_paused(tmp_path) is True
    assert configure(store) == first
    reopened = ContinuityStore(tmp_path)
    assert reopened.policy() == first
    second = configure(reopened, heartbeat_paused=False, expected_revision=1, request_id="resume")
    assert second == {"revision": 2, "heartbeat_paused": False}
    assert heartbeat_turns_paused(tmp_path) is False
    journal = reopened.status()["journal"]
    assert [row["sequence"] for row in journal] == [1, 2]
    assert [row["action"] for row in journal] == ["pause", "resume"]
    assert journal[0]["policy"] == first
    assert journal[1]["policy"] == second
    assert journal[0]["created_at"] <= journal[1]["created_at"]
    assert configure(reopened) == first
    assert reopened.policy() == second
    with pytest.raises(ConflictError, match="another continuity"):
        configure(reopened, heartbeat_paused=False)
    with pytest.raises(ConflictError, match="reload"):
        configure(reopened, request_id="stale")
    assert reopened.status()["journal"] == journal


def test_concurrent_policy_writers_serialize(tmp_path):
    store = ContinuityStore(tmp_path)
    def claim(index):
        try:
            return configure(ContinuityStore(tmp_path), request_id=str(index))
        except ConflictError:
            return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(claim, range(3)))
    assert sum(row is not None for row in rows) == 1
    assert store.policy()["revision"] == 1
    assert len(store.status()["journal"]) == 1


@pytest.mark.parametrize("changes", [{"heartbeat_paused": 1}, {"expected_revision": True}, {"expected_revision": -1}, {"request_id": ""}])
def test_policy_validation_does_not_write(changes, tmp_path):
    store = ContinuityStore(tmp_path)
    with pytest.raises(ValueError):
        configure(store, **changes)
    assert store.status()["journal"] == []
    assert store.policy()["revision"] == 0


def test_actual_slots_idempotency_tombstone_and_canonical_wal(tmp_path):
    store = ContinuityStore(tmp_path)
    added = store.append_anchor(slot="persona", text="My chosen name is Gideon.")
    assert added["slot"] == "persona"
    assert len(added["lines"]) == 1
    assert added["lines"][0]["text"] == "My chosen name is Gideon."
    assert added["lines"][0]["tombstoned"] is False
    assert added["lines"][0]["reinforcements"] == 1
    assert store.append_anchor(slot="persona", text="My chosen name is Gideon.") == added
    state = ContinuityStore(tmp_path).status()
    assert state["slots"]["persona"] == added["lines"]
    assert "My chosen name is Gideon." in state["context"]
    assert len(state["memory_events"]) == 1
    assert state["memory_events"][0]["memory_key"] == "slot.persona"
    assert state["memory_events"][0]["source"] == "user_explicit"
    removed = store.remove_anchor(slot="persona", text="My chosen name is Gideon.")
    assert removed["lines"][0]["tombstoned"] is True
    assert removed["lines"][0]["tombstoned_by"] == "human"
    assert store.append_anchor(slot="persona", text="My chosen name is Gideon.") == removed
    assert store.remove_anchor(slot="persona", text="My chosen name is Gideon.") == removed
    state = ContinuityStore(tmp_path).status()
    assert "My chosen name" not in state["context"]
    assert len(state["memory_events"]) == 2
    assert state["memory_events"][0]["id"] > state["memory_events"][1]["id"]
    assert state["journal"] == []
    assert (tmp_path / "memory.db").is_file()


def test_existing_slot_caps_do_not_silently_trim(tmp_path):
    store = ContinuityStore(tmp_path)
    first = store.append_anchor(slot="persona", text="x" * 300)
    with pytest.raises(ValueError, match="full"):
        store.append_anchor(slot="persona", text="y" * 200)
    assert store.status()["slots"]["persona"] == first["lines"]
    with pytest.raises(ValueError, match="limit"):
        store.append_anchor(slot="self_notes", text="z" * 501)
    with pytest.raises(ValueError):
        store.append_anchor(slot="preferences", text="Other")
    with pytest.raises(ValueError):
        store.remove_anchor(slot="persona", text="")
    assert store.status()["slots"]["self_notes"] == []


def test_actual_prompt_assembler_consumes_canonical_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.cognition.context import PromptAssembler
    from gideon.cognition.memory import MemoryJournal
    from gideon.cognition.vector_memory import SemanticArchive
    from gideon.extensions.skills import ProcedureLibrary
    store = ContinuityStore(tmp_path)
    store.append_anchor(slot="persona", text="Prefer precise answers.")
    store.append_anchor(slot="self_notes", text="Remember unfinished telescope calibration.")
    memory = MemoryJournal(workspace=tmp_path / "workspace")
    semantic = SemanticArchive(tmp_path / "memory.db")
    semantic.init()
    memory.vector_store = semantic
    try:
        assembler = PromptAssembler(memory=memory, skills=ProcedureLibrary(skills_path=tmp_path / "skills", install_builtins=False))
        sections = assembler._standing_memory(memory, "dashboard:continuity", "gideon")
        combined = "\n".join(sections.direct)
        assert "[MEMORY SLOTS]" in combined
        assert "Prefer precise answers." in combined
        assert "Remember unfinished telescope calibration." in combined
        assert semantic.get_semantic("slot.persona") is not None
        assert semantic.get_semantic("slot.self_notes") is not None
    finally:
        semantic.close()
    store.remove_anchor(slot="self_notes", text="Remember unfinished telescope calibration.")
    assert "unfinished telescope" not in store.status()["context"]


@pytest.mark.asyncio
async def test_real_heartbeat_pause_retains_file_and_rechecks_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.engine.heartbeat import HeartbeatService, heartbeat_path, is_keep_response
    store = ContinuityStore(tmp_path)
    configure(store)
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "# Heartbeat Tasks\n\n- Inspect continuity\n"
    path.write_text(content)
    heartbeat = HeartbeatService()
    await heartbeat._process_heartbeat_file()
    assert path.read_text() == content
    assert heartbeat._processing is False
    assert is_keep_response(await heartbeat._run_one_task("Inspect", ""))
    configure(store, heartbeat_paused=False, expected_revision=1, request_id="resume")
    assert not heartbeat_turns_paused(tmp_path)
    with pytest.raises(AssertionError):
        await heartbeat._run_one_task("Inspect", "")
    assert path.read_text() == content
    configure(store, expected_revision=2, request_id="pause-again")
    assert is_keep_response(await heartbeat._run_one_task("Inspect", ""))
    heartbeat._tick = 1
    await heartbeat._beat()
    assert path.read_text() == content
    assert heartbeat._processing is False
    assert [row["action"] for row in store.status()["journal"]] == ["pause", "resume", "pause"]


@pytest.mark.asyncio
async def test_actual_http_slot_policy_and_conflicts(tmp_path):
    app = web.Application()
    register(app, home=tmp_path)
    async with TestClient(TestServer(app)) as client:
        first = await (await client.get(PREFIX)).json()
        assert first["heartbeat_paused"] is False
        payload = dict(heartbeat_paused=True, expected_revision=0, request_id="pause")
        response = await client.put(PREFIX, json=payload)
        assert response.status == 200
        assert (await response.json())["revision"] == 1
        assert (await client.put(PREFIX, json={**payload, "request_id": "stale"})).status == 409
        response = await client.post(PREFIX + "/anchors", json={"slot": "self_notes", "text": "Retain calibration context"})
        assert response.status == 200
        added = await response.json()
        assert added["lines"][0]["text"] == "Retain calibration context"
        response = await client.post(PREFIX + "/anchors/remove", json={"slot": "self_notes", "text": "Retain calibration context"})
        assert (await response.json())["lines"][0]["tombstoned"] is True
        assert (await client.post(PREFIX + "/anchors", json={"slot": "bad", "text": "bad"})).status == 400
        assert (await client.put(PREFIX, json=[])).status == 400
        assert (await client.put(PREFIX, json={**payload, "home": "other"})).status == 400
    assert ContinuityStore(tmp_path / "other").status()["slots"]["self_notes"] == []


@pytest.mark.asyncio
async def test_native_controls_use_same_policy_and_cannot_remove_human_anchors(tmp_path):
    provider = IdentityToolProvider(tmp_path)
    token = set_current_session_key("dashboard:continuity")
    try:
        configured = await provider.invoke("identity_continuity_configure", dict(heartbeat_paused=True, expected_revision=0, request_id="native"))
        assert configured.success
        assert heartbeat_turns_paused(tmp_path)
        added = await provider.invoke("identity_continuity_append_anchor", {"slot": "persona", "text": "Careful reasoning"})
        assert added.success
        assert ContinuityStore(tmp_path).status()["memory_events"][0]["source"] == "agent_explicit"
        result = await provider.invoke("identity_continuity_status", {})
        assert result.success
        assert "Careful reasoning" in json.loads(result.output)["context"]
        rejected = await provider.invoke("identity_continuity_remove_anchor", {"slot": "persona", "text": "Careful reasoning"})
        assert not rejected.success
        assert "Unknown" in rejected.error
        assert ContinuityStore(tmp_path).status()["slots"]["persona"][0]["tombstoned"] is False
    finally:
        reset_current_session_key(token)

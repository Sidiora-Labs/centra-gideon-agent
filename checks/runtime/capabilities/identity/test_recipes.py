import asyncio
import json
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.workspace.capabilities.identity.recipes import RecipeStore, READ_TOOLS
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.interfaces.dashboard.handlers.capabilities_identity_recipes import register, PREFIX


def setup(home):
    directory = home / "capabilities/identity"
    goals = GoalStore(directory / "goals.sqlite3")
    goal = goals.save_goal(title="Build telescope", request_id="goal")
    return RecipeStore(directory / "recipes.sqlite3"), IdentityToolProvider(home), goal


def save(store, **changes):
    steps = [{"id": "goals", "tool": "identity_goals_list_goals", "arguments": {}},
             {"id": "detail", "tool": "identity_goals_get_goal", "arguments": {"id": {"$ref": "goals#/0/id"}}}]
    return store.save(**{"title": "Inspect human goal", "steps": steps, "request_id": "recipe", **changes})


def test_recipe_versions_restore_replay_and_restart(tmp_path):
    store, _, _ = setup(tmp_path)
    first = save(store)
    assert first["revision"] == 1
    assert first["enabled"] is True
    assert save(store) == first
    second = save(store, id=first["id"], expected_revision=1, request_id="edit", title="Inspect changed")
    assert second["revision"] == 2
    assert store.history(first["id"]) == [first, second]
    restored = store.restore(id=first["id"], revision=1, expected_revision=2, request_id="restore")
    assert restored["revision"] == 3
    assert restored["title"] == first["title"]
    assert restored["steps"] == first["steps"]
    assert store.restore(id=first["id"], revision=1, expected_revision=2, request_id="restore") == restored
    reopened = RecipeStore(store.path)
    assert reopened.get(first["id"]) == restored
    assert reopened.list() == [restored]
    assert reopened.history(first["id"]) == [first, second, restored]
    with pytest.raises(ConflictError, match="reload"):
        save(store, id=first["id"], expected_revision=1, request_id="stale")
    with pytest.raises(ConflictError, match="another recipe"):
        save(store, title="Changed with same request")
    with pytest.raises(KeyError):
        store.restore(id=first["id"], revision=999, expected_revision=3, request_id="bad-restore")
    assert store.get(first["id"]) == restored


@pytest.mark.parametrize("steps", [
    [], [{"id": "one", "tool": "identity_goals_list_goals", "arguments": {}}] * 6,
    [{"id": "one", "tool": "identity_goals_save_goal", "arguments": {}}],
    [{"id": "one", "tool": "identity_recipe_advance", "arguments": {}}],
    [{"id": "one", "tool": "shell", "arguments": {}}],
    [{"id": "one", "tool": "identity_goals_get_goal", "arguments": {"id": {"$ref": "future#/id"}}}],
    [{"id": "one", "tool": "identity_goals_get_goal", "arguments": {"id": {"$ref": "one#/id"}}}],
    [{"id": "bad/name", "tool": "identity_goals_list_goals", "arguments": {}}],
    [{"id": "one", "tool": "identity_goals_list_goals", "arguments": []}],
    [{"id": "one", "tool": "identity_goals_list_goals", "arguments": {}, "provider": "other"}],
])
def test_definition_validation_refuses_unbounded_or_unapproved_steps(tmp_path, steps):
    store, _, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        save(store, steps=steps)
    assert store.list() == []
    assert store.list_runs() == []


def test_disabled_and_stale_begin_refused(tmp_path):
    store, _, _ = setup(tmp_path)
    recipe = save(store, enabled=False)
    with pytest.raises(ConflictError, match="disabled"):
        store.begin(recipe_id=recipe["id"], revision=1, request_id="run")
    assert store.list_runs() == []
    save(store, id=recipe["id"], expected_revision=1, request_id="enable")
    with pytest.raises(ConflictError, match="revision changed"):
        store.begin(recipe_id=recipe["id"], revision=1, request_id="run")
    run = store.begin(recipe_id=recipe["id"], revision=2, request_id="run")
    assert run["status"] == "ready"
    assert run["next_index"] == 0
    assert run["steps"] == []
    assert run["recipe_snapshot"]["revision"] == 2
    assert store.get_run(run["id"]) == run
    assert store.begin(recipe_id=recipe["id"], revision=2, request_id="run") == run
    assert store.list_runs() == [run]


@pytest.mark.asyncio
async def test_actual_guarded_reads_bind_outputs_and_retries_do_not_repeat_steps(tmp_path):
    store, provider, goal = setup(tmp_path)
    recipe = save(store)
    run = store.begin(recipe_id=recipe["id"], revision=1, request_id="run")
    token = set_current_session_key("dashboard:recipes")
    try:
        first = await store.advance(run["id"], 0, provider)
        assert first["status"] == "ready"
        assert first["next_index"] == 1
        assert first["steps"][0]["output"] == [goal]
        assert first["steps"][0]["tool"] == "identity_goals_list_goals"
        assert await store.advance(run["id"], 0, provider) == first
        assert store.begin(recipe_id=recipe["id"], revision=1, request_id="run") == first
        completed = await RecipeStore(store.path).advance(run["id"], 1, provider)
        assert completed["status"] == "completed"
        assert completed["next_index"] == 2
        assert completed["steps"][1]["output"] == goal
        assert completed["steps"][1]["id"] == "detail"
        assert await store.advance(run["id"], 1, provider) == completed
        assert await store.advance(run["id"], 2, provider) == completed
        assert RecipeStore(store.path).get_run(run["id"]) == completed
        assert store.list_runs() == [completed]
    finally:
        reset_current_session_key(token)


@pytest.mark.asyncio
async def test_actual_live_recipe_revocation_between_steps(tmp_path):
    store, provider, _ = setup(tmp_path)
    recipe = save(store)
    run = store.begin(recipe_id=recipe["id"], revision=1, request_id="run")
    token = set_current_session_key("dashboard:recipes")
    try:
        first = await store.advance(run["id"], 0, provider)
        assert first["next_index"] == 1
        disabled = save(store, id=recipe["id"], expected_revision=1, enabled=False, request_id="disable")
        assert disabled["enabled"] is False
        stopped = await store.advance(run["id"], 1, provider)
        assert stopped["status"] == "revoked"
        assert stopped["steps"] == first["steps"]
        assert stopped["recipe_snapshot"] == recipe
        assert stopped["recipe_revision"] == 1
        assert stopped["next_index"] == 1
    finally:
        reset_current_session_key(token)


@pytest.mark.asyncio
async def test_cancellation_and_session_revocation_are_actual_dispatch_boundaries(tmp_path):
    store, provider, _ = setup(tmp_path)
    recipe = save(store)
    run = store.begin(recipe_id=recipe["id"], revision=1, request_id="cancel")
    cancelled = store.cancel(run["id"])
    assert cancelled["status"] == "cancelled"
    assert store.cancel(run["id"]) == cancelled
    assert await store.advance(run["id"], 0, provider) == cancelled
    run = store.begin(recipe_id=recipe["id"], revision=1, request_id="remote")
    token = set_current_session_key("telegram:outside")
    try:
        failed = await store.advance(run["id"], 0, provider)
        assert failed["status"] == "failed"
        assert failed["next_index"] == 1
        assert failed["steps"][0]["status"] == "failed"
        assert "private conversation" in failed["steps"][0]["error"]
        assert "output" not in failed["steps"][0]
        assert len(failed["steps"]) == 1
    finally:
        reset_current_session_key(token)


@pytest.mark.asyncio
async def test_concurrent_step_claim_has_one_actual_outcome(tmp_path):
    store, provider, _ = setup(tmp_path)
    recipe = save(store)
    run = store.begin(recipe_id=recipe["id"], revision=1, request_id="run")
    token = set_current_session_key("dashboard:recipes")
    try:
        results = await asyncio.gather(store.advance(run["id"], 0, provider), store.advance(run["id"], 0, provider))
        assert {row["status"] for row in results} <= {"running", "ready"}
        persisted = store.get_run(run["id"])
        assert persisted["next_index"] == 1
        assert len(persisted["steps"]) == 1
        with pytest.raises(ConflictError, match="step changed"):
            await store.advance(run["id"], 3, provider)
        assert store.get_run(run["id"]) == persisted
    finally:
        reset_current_session_key(token)


@pytest.mark.asyncio
async def test_actual_binding_and_tool_schema_failures_preserve_partial_results(tmp_path):
    store, provider, goal = setup(tmp_path)
    recipe = save(store, steps=[{"id": "goals", "tool": "identity_goals_list_goals", "arguments": {}}, {"id": "missing", "tool": "identity_goals_get_goal", "arguments": {"id": {"$ref": "goals#/99/id"}}}])
    run = store.begin(recipe_id=recipe["id"], revision=1, request_id="run")
    token = set_current_session_key("dashboard:recipes")
    try:
        first = await store.advance(run["id"], 0, provider)
        final = await store.advance(run["id"], 1, provider)
        assert final["status"] == "failed"
        assert final["steps"][0] == first["steps"][0]
        assert final["steps"][0]["output"] == [goal]
        assert final["steps"][1]["status"] == "failed"
        assert "output" not in final["steps"][1]
        invalid = save(store, request_id="bad-args", steps=[{"id": "one", "tool": "identity_goals_get_goal", "arguments": {"home": "/tmp"}}])
        started = store.begin(recipe_id=invalid["id"], revision=1, request_id="bad-run")
        result = await store.advance(started["id"], 0, provider)
        assert result["status"] == "failed"
        assert "Invalid identity arguments" in result["steps"][0]["error"]
    finally:
        reset_current_session_key(token)


@pytest.mark.asyncio
async def test_actual_http_author_restore_and_dispatch_bound_provider(tmp_path):
    _, _, goal = setup(tmp_path)
    app = web.Application()
    register(app, home=tmp_path)
    async with TestClient(TestServer(app)) as client:
        catalog = await (await client.get(PREFIX + "/catalog")).json()
        assert set(catalog["tools"]) == READ_TOOLS
        assert catalog["max_steps"] == 5
        body = dict(title="One read", steps=[{"id": "read", "tool": "identity_goals_get_goal", "arguments": {"id": goal["id"]}}], request_id="http")
        response = await client.post(PREFIX, json=body)
        assert response.status == 200
        recipe = await response.json()
        assert await (await client.get(PREFIX + "/" + recipe["id"])).json() == recipe
        assert await (await client.get(PREFIX + "/" + recipe["id"] + "/history")).json() == [recipe]
        run = await (await client.post(PREFIX + "/begin", json=dict(recipe_id=recipe["id"], revision=1, request_id="run"))).json()
        assert run["status"] == "ready"
        response = await client.post(PREFIX + "/advance", json=dict(run_id=run["id"], expected_index=0))
        assert response.status == 200
        completed = await response.json()
        assert completed["status"] == "completed"
        assert completed["steps"][0]["output"] == goal
        assert await (await client.get(PREFIX + "/runs/" + run["id"])).json() == completed
        assert await (await client.get(PREFIX + "/runs")).json() == [completed]
        assert (await client.post(PREFIX + "/advance", json=dict(run_id=run["id"], expected_index=0, provider="other"))).status == 400
        assert (await client.get(PREFIX + "/missing")).status == 404


@pytest.mark.asyncio
async def test_native_recipe_authoring_and_actual_nested_read_delegate(tmp_path):
    store, provider, goal = setup(tmp_path)
    token = set_current_session_key("dashboard:recipe-author")
    try:
        result = await provider.invoke("identity_recipe_save", {"title": "Native recipe", "steps": [{"id": "goal", "tool": "identity_goals_get_goal", "arguments": {"id": goal["id"]}}], "request_id": "native"})
        assert result.success
        recipe = json.loads(result.output)
        result = await provider.invoke("identity_recipe_history", {"id": recipe["id"]})
        assert json.loads(result.output) == [recipe]
        result = await provider.invoke("identity_recipe_begin", {"recipe_id": recipe["id"], "revision": 1, "request_id": "run"})
        assert result.success
        run = json.loads(result.output)
        result = await provider.invoke("identity_recipe_advance", {"run_id": run["id"], "expected_index": 0})
        assert result.success
        completed = json.loads(result.output)
        assert completed["status"] == "completed"
        assert completed["steps"][0]["output"] == goal
        assert store.get_run(run["id"]) == completed
        result = await provider.invoke("identity_recipe_list", {})
        assert json.loads(result.output) == [recipe]
    finally:
        reset_current_session_key(token)

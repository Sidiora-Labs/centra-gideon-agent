import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.integrations.mcp_core import (
    reset_current_session_key,
    set_current_session_key,
)
from gideon.interfaces.dashboard.handlers.capabilities_identity_fidelity import (
    PREFIX,
    register,
)
from gideon.workspace.capabilities.identity.fidelity import FidelityStore
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.workspace.capabilities.identity.tools import IdentityToolProvider
from gideon.workspace.capabilities.identity.twin import TwinStore


def setup_sources(path):
    twin = TwinStore(path / "twin.sqlite3")
    state = twin.save_document(
        title="Values", text="I value curiosity and honesty.", expected_revision=0
    )
    return twin, state["documents"][0], FidelityStore(path / "fidelity.sqlite3")


def case(store, source, **changes):
    return store.save_case(
        prompt="What matters to me?",
        source_ids=[source["id"]],
        request_id="case",
        rules=changes.pop("rules", [{"type": "contains", "value": "curiosity"}]),
        **changes,
    )


def test_supplied_observation_has_explicit_non_provider_provenance(tmp_path):
    twin, source, store = setup_sources(tmp_path)
    record = case(store, source)
    run = store.record_observation(
        case_id=record["id"], answer="Curiosity matters.", request_id="one"
    )
    assert run["origin"] == "supplied_observation"
    assert run["status"] == "completed"
    assert run["passed"] is True
    assert run["provider"] is None
    assert run["model"] is None
    assert run["results"] == [{"rule": record["rules"][0], "passed": True}]
    assert run["source_snapshot"] == [
        {"id": source["id"], "title": source["title"], "text": source["text"]}
    ]
    assert run["twin_revision"] == twin.snapshot()["revision"]
    reopened = FidelityStore(store.path)
    assert reopened.get_run(run["id"]) == run
    assert reopened.list_runs() == [run]
    assert reopened.get_case(record["id"]) == record
    assert reopened.list_cases() == [record]


@pytest.mark.parametrize(
    "rule,answer,expected",
    [
        ({"type": "contains", "value": "Curiosity"}, "CURIOSITY", True),
        (
            {"type": "contains", "value": "Curiosity", "case_sensitive": True},
            "curiosity",
            False,
        ),
        ({"type": "not_contains", "value": "dishonesty"}, "honesty", True),
        ({"type": "not_contains", "value": "dishonesty"}, "Dishonesty", False),
        ({"type": "equals", "value": "Honesty"}, " honesty ", True),
        ({"type": "equals", "value": "Honesty"}, "Honesty is useful", False),
    ],
)
def test_actual_existing_assertion_rules(rule, answer, expected, tmp_path):
    _, source, store = setup_sources(tmp_path)
    record = case(store, source, rules=[rule])
    run = store.record_observation(
        case_id=record["id"], answer=answer, request_id="observation"
    )
    assert run["passed"] is expected
    assert run["results"][0]["passed"] is expected
    assert run["answer"] == answer
    assert run["origin"] == "supplied_observation"


def test_all_rules_must_pass_and_full_evidence_remains(tmp_path):
    _, source, store = setup_sources(tmp_path)
    rules = [
        {"type": "contains", "value": "curiosity"},
        {"type": "not_contains", "value": "honesty"},
    ]
    record = case(store, source, rules=rules)
    run = store.record_observation(
        case_id=record["id"], answer="Curiosity and honesty.", request_id="one"
    )
    assert run["passed"] is False
    assert [result["passed"] for result in run["results"]] == [True, False]
    assert run["case_snapshot"]["rules"] == rules
    assert run["answer"] == "Curiosity and honesty."


def test_snapshots_survive_case_and_source_edits(tmp_path):
    twin, source, store = setup_sources(tmp_path)
    original = case(store, source)
    run = store.record_observation(
        case_id=original["id"], answer="Curiosity", request_id="one"
    )
    changed = store.save_case(
        id=original["id"],
        prompt=original["prompt"],
        source_ids=original["source_ids"],
        rules=[{"type": "equals", "value": "honesty"}],
        expected_revision=1,
    )
    assert changed["revision"] == 2
    twin.save_document(**{**source, "text": "I value patience."}, expected_revision=1)
    preserved = store.get_run(run["id"])
    assert preserved["case_snapshot"] == original
    assert preserved["source_snapshot"][0]["text"] == "I value curiosity and honesty."
    assert preserved["case_revision"] == 1
    assert store.get_case(original["id"])["rules"] == [
        {"type": "equals", "value": "honesty"}
    ]
    assert preserved["passed"] is True


def test_observation_replay_conflicts_without_duplicates(tmp_path):
    _, source, store = setup_sources(tmp_path)
    record = case(store, source)
    run = store.record_observation(
        case_id=record["id"], answer="Curiosity", request_id="one"
    )
    assert (
        store.record_observation(
            case_id=record["id"], answer="Curiosity", request_id="one"
        )
        == run
    )
    with pytest.raises(ConflictError, match="different evaluation"):
        store.record_observation(
            case_id=record["id"], answer="Honesty", request_id="one"
        )
    with pytest.raises(ConflictError):
        store.begin_run(case_id=record["id"], request_id="one")
    assert store.list_runs() == [run]


def test_provider_claim_replay_and_failure_never_passes(tmp_path):
    _, source, store = setup_sources(tmp_path)
    record = case(store, source)
    run = store.begin_run(case_id=record["id"], request_id="run")
    assert run["status"] == "running"
    assert run["passed"] is None
    assert run["answer"] is None
    assert run["results"] == []
    assert run["origin"] == "provider"
    with pytest.raises(ConflictError, match="already running"):
        store.begin_run(case_id=record["id"], request_id="run")
    with pytest.raises(ValueError):
        store.complete_run(run["id"], "")
    failed = store.fail_run(run["id"], "No configured provider")
    assert failed["status"] == "unavailable"
    assert failed["passed"] is None
    assert failed["answer"] is None
    assert failed["model"] is None
    assert store.begin_run(case_id=record["id"], request_id="run") == failed
    with pytest.raises(ConflictError, match="settled"):
        store.fail_run(run["id"], "A second failure")
    assert FidelityStore(store.path).get_run(run["id"]) == failed


def test_concurrent_provider_requests_have_one_claim(tmp_path):
    _, source, store = setup_sources(tmp_path)
    record = case(store, source)

    def claim(_):
        try:
            return store.begin_run(case_id=record["id"], request_id="once")
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert len([row for row in claims if row]) == 1
    assert len(store.list_runs()) == 1
    assert store.list_runs()[0]["status"] == "running"


@pytest.mark.parametrize(
    "rules",
    [
        [],
        [{"type": "judge", "value": "Looks right"}],
        [{"type": "regex", "value": "(a+)+$"}],
        [{"type": "contains", "value": ""}],
        [{"type": "equals", "value": "a", "extra": True}],
        [{"type": "equals", "value": "a", "case_sensitive": 1}],
    ],
)
def test_unsupported_or_vacuous_expectations_rejected(tmp_path, rules):
    _, source, store = setup_sources(tmp_path)
    with pytest.raises(ValueError):
        case(store, source, rules=rules)
    assert store.list_cases() == []
    assert store.list_runs() == []


def test_private_disabled_foreign_and_missing_sources_refused(tmp_path):
    twin, source, store = setup_sources(tmp_path / "local")
    _, foreign, _ = setup_sources(tmp_path / "other")
    for ids in [[], [source["id"], source["id"]], [foreign["id"]], ["missing"], [None]]:
        with pytest.raises(ValueError):
            store.save_case(
                prompt="Question",
                source_ids=ids,
                rules=[{"type": "contains", "value": "x"}],
            )
    record = case(store, source)
    twin.save_document(**{**source, "private": True}, expected_revision=1)
    with pytest.raises(ValueError, match="non-private"):
        store.record_observation(
            case_id=record["id"], answer="Curiosity", request_id="one"
        )
    twin.save_document(**{**source, "enabled": False}, expected_revision=2)
    with pytest.raises(ValueError):
        store.begin_run(case_id=record["id"], request_id="two")
    assert store.list_runs() == []


def test_case_creation_replay_survives_restart_and_refuses_changed_payload(tmp_path):
    _, source, store = setup_sources(tmp_path)
    original = case(store, source)
    reopened = FidelityStore(store.path)
    assert case(reopened, source) == original
    assert reopened.list_cases() == [original]
    with pytest.raises(ConflictError, match="different case"):
        case(reopened, source, rules=[{"type": "equals", "value": "honesty"}])
    with pytest.raises(ValueError, match="request_id"):
        store.save_case(
            prompt="Question", source_ids=[source["id"]], rules=original["rules"]
        )
    assert reopened.list_cases() == [original]


@pytest.mark.asyncio
async def test_actual_http_cases_observations_runs_and_conflicts(tmp_path):
    _, source, _ = setup_sources(tmp_path)
    app = web.Application()
    register(app, store_path=tmp_path / "fidelity.sqlite3")
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            PREFIX + "/cases",
            json={
                "request_id": "http-case",
                "prompt": "What matters?",
                "source_ids": [source["id"]],
                "rules": [{"type": "contains", "value": "curiosity"}],
            },
        )
        assert response.status == 200
        record = await response.json()
        response = await client.get(PREFIX + "/cases/" + record["id"])
        assert await response.json() == record
        body = {
            "case_id": record["id"],
            "answer": "I value curiosity.",
            "request_id": "http",
        }
        response = await client.post(PREFIX + "/observations", json=body)
        assert response.status == 200
        run = await response.json()
        assert run["origin"] == "supplied_observation"
        assert run["passed"] is True
        response = await client.get(PREFIX + "/runs/" + run["id"])
        assert await response.json() == run
        response = await client.get(PREFIX + "/runs")
        assert await response.json() == [run]
        response = await client.post(
            PREFIX + "/observations", json={**body, "answer": "Different"}
        )
        assert response.status == 409
        response = await client.post(
            PREFIX + "/cases",
            json={
                "prompt": "Question",
                "source_ids": [source["id"]],
                "rules": [{"type": "judge", "value": "anything"}],
            },
        )
        assert response.status == 400
        response = await client.post(
            PREFIX + "/run",
            json={"case_id": record["id"], "request_id": "http", "model": "external"},
        )
        assert response.status == 400
        response = await client.get(PREFIX + "/runs/missing")
        assert response.status == 404
    assert FidelityStore(tmp_path / "fidelity.sqlite3").get_run(run["id"]) == run


@pytest.mark.asyncio
async def test_native_fidelity_observation_and_revoked_private_source(tmp_path):
    _, source, store = setup_sources(tmp_path / "capabilities/identity")
    provider = IdentityToolProvider(tmp_path)
    token = set_current_session_key("dashboard:fidelity")
    try:
        result = await provider.invoke(
            "identity_fidelity_save_case",
            {
                "request_id": "tool-case",
                "prompt": "Values?",
                "source_ids": [source["id"]],
                "rules": [{"type": "contains", "value": "curiosity"}],
            },
        )
        assert result.success, result.error
        record = json.loads(result.output)
        result = await provider.invoke(
            "identity_fidelity_observe",
            {"case_id": record["id"], "answer": "Curiosity", "request_id": "tool"},
        )
        assert result.success, result.error
        run = json.loads(result.output)
        assert run["origin"] == "supplied_observation"
        assert run["model"] is None
        assert run["passed"]
        assert (
            json.loads(
                (
                    await provider.invoke(
                        "identity_fidelity_get_case", {"id": record["id"]}
                    )
                ).output
            )
            == record
        )
        twin = TwinStore(store.path.parent / "twin.sqlite3")
        twin.save_document(**{**source, "private": True}, expected_revision=1)
        for name in ["identity_fidelity_list_cases", "identity_fidelity_list_runs"]:
            result = await provider.invoke(name, {})
            assert result.success
            assert json.loads(result.output) == []
        result = await provider.invoke("identity_fidelity_get_run", {"id": run["id"]})
        assert not result.success
        assert "Accessible fidelity record" in result.error
        assert store.get_run(run["id"])["source_snapshot"][0]["text"] == source["text"]
    finally:
        reset_current_session_key(token)

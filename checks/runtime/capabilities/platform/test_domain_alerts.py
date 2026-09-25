import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.inbox import InboxStore
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, use_ephemeral_secret
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.platform.domain_alerts import conditions, inventory, readiness, scan
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore

from checks.runtime.capabilities.platform.test_catalog import application

PATH = "/api/capabilities/platform/domain-readiness"
NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    return tmp_path


def setup(home):
    goals = GoalStore(home / "capabilities/identity/goals.sqlite3")
    goal = goals.save_goal(
        title="Private goal title",
        target_date="2026-09-20",
        request_id="one",
    )
    state = ConsoleState(ConversationDirectory(AppConfig.load()), 0)
    state._notification_log.clear()
    inbox = InboxStore()
    inbox.load()
    return goals, goal, state, inbox


def intervention_plan(**changes):
    return {
        "request_id": "plan-one",
        "name": "Private activity",
        "instructions": "Private instructions",
        "kind": "activity",
        "source": "self",
        "timezone": "UTC",
        "start_date": "2026-09-19",
        "end_date": None,
        "weekdays": list(range(7)),
        **changes,
    }


def intervention_record(**changes):
    return {
        "request_id": "record-one",
        "date": "2026-09-24",
        "status": "completed",
        "observed_at": "2026-09-24T12:00:00+00:00",
        "notes": "Private notes",
        **changes,
    }


def test_attention_deduplicates_resolves_and_resurfaces_changed_evidence(home):
    goals, goal, state, inbox = setup(home)
    assert scan(state, inbox, NOW)["conditions"] == 1
    assert len(inbox.items) == 1
    item = next(iter(inbox.items.values()))
    assert item.refs["domain_source"] == goal["id"]
    assert item.refs["url"] == "#/capabilities/identity"
    assert "Private goal title" not in item.message
    assert len(state._notification_log) == 1
    scan(state, inbox, NOW)
    assert len(inbox.items) == 1
    assert len(state._notification_log) == 1
    inbox.update(item.id, status="dismissed")
    restored = InboxStore()
    restored.load()
    scan(state, restored, NOW)
    assert len(restored.items) == 1
    assert len(state._notification_log) == 1
    goal = goals.save_goal(
        id=goal["id"],
        expected_revision=1,
        title="Private revised",
        target_date="2026-09-21",
        request_id="two",
    )
    scan(state, restored, NOW)
    assert len(restored.items) == 2
    assert len(state._notification_log) == 2
    current = next(row for row in restored.items.values() if row.status == "pending")
    assert current.refs["domain_fingerprint"] != item.refs["domain_fingerprint"]
    goals.save_goal(
        id=goal["id"],
        expected_revision=2,
        title="Done",
        status="completed",
        target_date="2026-09-21",
        request_id="three",
    )
    assert scan(state, restored, NOW)["conditions"] == 0
    assert restored.items[current.id].status == "handled"
    assert len(state._notification_log) == 2


def test_recording_gap_means_unknown_and_never_skipped(home):
    store = InterventionStore(home)
    parent = store.create_plan(intervention_plan())
    store.record(parent["id"], intervention_record())
    rows = conditions(home, NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row["detector"] == "recording_gap"
    assert row["source_id"] == parent["id"]
    assert "2026-09-24" not in row["evidence"]["unrecorded_dates"]
    assert len(row["evidence"]["unrecorded_dates"]) == 6
    assert "do not mean" in row["body"]
    assert conditions(home, NOW)[0]["fingerprint"] == row["fingerprint"]
    store.update_plan(
        parent["id"], {"revision": 1, "request_id": "archive", "archived": True}
    )
    assert conditions(home, NOW) == []


def test_readiness_is_read_only_and_distinguishes_unconfigured_unavailable(home, tmp_path):
    assert {row["state"] for row in readiness(home)} == {"unconfigured"}
    assert not (home / "capabilities").exists()
    setup(home)
    InterventionStore(home)
    assert {row["state"] for row in readiness(home)} == {"ready"}
    path = home / "capabilities/wellbeing.sqlite3"
    path.write_bytes(b"broken database")
    assert readiness(home)[1]["state"] == "unavailable"
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    assert readiness(home)[1]["state"] == "unavailable"
    with pytest.raises(ValueError, match="escapes"):
        conditions(home, NOW)


def test_relationship_detector_requires_complete_canonical_coverage(home):
    from gideon.workspace.capabilities.communications.evidence import ingest
    from gideon.workspace.capabilities.communications.store import PeopleStore
    from checks.runtime.capabilities.communications.test_evidence import batch, message

    store = PeopleStore(home / "capabilities/communications")
    person = store.save(
        {"name": "Private friend", "notes": "Private relationship notes", "cadence_days": 3}
    )
    ingest(store, batch(person["id"]))
    rows = conditions(home, NOW)
    assert len(rows) == 1
    assert rows[0]["detector"] == "unanswered_thread"
    assert rows[0]["evidence"]["message_id"] == "message-one"
    assert rows[0]["source_id"] == person["id"]
    assert "Can we meet" not in json.dumps(rows)
    assert "Private friend" not in json.dumps(rows)
    incomplete = PeopleStore(home / "capabilities/communications-incomplete")
    other = incomplete.save({"name": "Other"})
    ingest(incomplete, batch(other["id"], incoming_complete=False))
    assert conditions(home, NOW + timedelta(days=2)) == []
    later = NOW + timedelta(hours=1)
    ingest(
        store,
        batch(
            person["id"],
            captured_at=later.isoformat(),
            coverage_end=later.isoformat(),
            messages=[
                message(
                    person["id"],
                    external_id="reply",
                    occurred_at=NOW.isoformat(),
                    direction="outbound",
                )
            ],
        ),
    )
    assert conditions(home, later) == []


@pytest.mark.asyncio
async def test_opted_in_task_quality_excludes_transport_failures(home):
    from checks.runtime.capabilities.platform.test_cadence import evidence, opt, trigger

    value = trigger()
    opt(value)
    await evidence(value, [False] * 5, now=NOW.timestamp())
    rows = conditions(home, NOW)
    assert len(rows) == 1
    assert rows[0]["detector"] == "task_quality"
    assert rows[0]["evidence"]["failures"] == 5
    assert len(rows[0]["evidence"]["run_ids"]) == 5
    fingerprint = rows[0]["fingerprint"]
    await evidence(
        value,
        [False] * 5,
        now=NOW.timestamp() + 1,
        offset=20,
        error=ConnectionError("transport unavailable"),
    )
    assert conditions(home, NOW + timedelta(seconds=1))[0]["fingerprint"] == fingerprint
    await evidence(value, [True] * 3, now=NOW.timestamp() + 10, offset=1)
    assert conditions(home, NOW + timedelta(seconds=10)) == []


def test_learning_probe_uses_flush_journal_without_error_details(home):
    from gideon.cognition.learning.staging import FlushOutcome, StagingStore

    store = StagingStore(home)
    store.record_flush(
        cadence="daily",
        outcome=FlushOutcome.FLUSH_ERROR,
        detail="private raw provider failure",
    )
    store.close()
    now = datetime.now(timezone.utc)
    rows = conditions(home, now)
    assert len(rows) == 1
    assert rows[0]["detector"] == "learning_health"
    assert rows[0]["evidence"]["errors"] == 1
    assert "private raw" not in json.dumps(rows)
    first = rows[0]["fingerprint"]
    store = StagingStore(home)
    store.record_flush(cadence="daily", outcome=FlushOutcome.FLUSH_ERROR)
    store.close()
    assert conditions(home, now)[0]["fingerprint"] != first


def test_crash_detector_reports_artifact_without_private_payload(home):
    from gideon.operations.resilience.crashes import record_crash

    path = record_crash(
        "turn",
        RuntimeError("private crash details"),
        session_key="private-session",
        now=NOW.timestamp() - 10,
    )
    assert path and path.exists()
    rows = conditions(home, NOW)
    assert len(rows) == 1
    assert rows[0]["detector"] == "recorded_crash"
    assert rows[0]["source_id"] == path.name
    assert rows[0]["title"] == "A runtime crash was recorded"
    assert "private-session" not in json.dumps(rows)
    assert "private crash details" not in json.dumps(rows)
    path.unlink()
    assert conditions(home, NOW) == []


@pytest.mark.asyncio
async def test_authenticated_http_and_native_use_same_inventory(home):
    from gideon.integrations.mcp_core import reset_current_session_key, set_current_session_key
    from gideon.workspace.capabilities.platform.tools import create_provider

    _, _, state, _ = setup(home)
    app = application(dynamic=False)
    app["state"] = state
    async with TestClient(TestServer(app)) as client:
        token = generate_token("domain-owner")
        response = await client.get(PATH, params={"token": token})
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert await response.json() == inventory()
        response = await client.post(PATH, params={"token": token})
        assert response.status == 200
        assert len(state._notification_log) == 1
        assert (await client.get(PATH, params={"unknown": "value"})).status == 422
        restricted = generate_token("domain-owner", app="restricted")
        assert (await client.get(PATH, params={"token": restricted})).status == 403
    token = set_current_session_key("dashboard:domains")
    try:
        result = await create_provider().invoke("platform_domain_readiness", {})
        assert result.success
        assert json.loads(result.output) == inventory()
        invalid = await create_provider().invoke("platform_domain_readiness", {"extra": True})
        assert invalid.success is False
    finally:
        reset_current_session_key(token)

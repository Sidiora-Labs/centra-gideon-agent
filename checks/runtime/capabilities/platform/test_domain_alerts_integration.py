"""Shared runtime wiring for canonical personal-domain observations."""

import pytest

from gideon.cognition.proactive.collect import collect_all
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.inbox import InboxStore
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations.resilience.doctor import (
    DoctorContext,
    all_probes,
    run_capability,
)
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore
from gideon.workspace.notification_kinds import kind_for_legacy, resolve_kind


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    return tmp_path


def live_state():
    state = ConsoleState(ConversationDirectory(AppConfig.load()), 0)
    state._notification_log.clear()
    return state


def test_collect_all_projects_real_goal_evidence_once_and_resolves_it(home):
    goals = GoalStore(home / "capabilities/identity/goals.sqlite3")
    goal = goals.save_goal(
        title="Private overdue goal",
        target_date="2020-01-01",
        request_id="create-overdue",
    )
    state = live_state()
    inbox = InboxStore()
    inbox.load()

    collected = collect_all(state=state, inbox_store=inbox, include_runs=False)
    assert len(collected) == 1
    assert collected[0].source == "inbox"
    assert collected[0].source_id in inbox.items
    item = inbox.items[collected[0].source_id]
    assert item.refs["domain_source"] == goal["id"]
    assert item.refs["evidence"] == {"revision": 1, "target_date": "2020-01-01"}
    assert item.refs["url"] == "#/capabilities/identity"
    assert "Private overdue goal" not in item.message
    assert len(state._notification_log) == 1
    assert state._notification_log[0]["kind"] == "personal_domain_alert"

    repeated = collect_all(state=state, inbox_store=inbox, include_runs=False)
    assert [row.source_id for row in repeated] == [item.id]
    assert len(inbox.items) == 1
    assert len(state._notification_log) == 1

    goals.save_goal(
        id=goal["id"],
        expected_revision=1,
        title="Completed goal",
        target_date="2020-01-01",
        status="completed",
        request_id="complete-overdue",
    )
    assert collect_all(state=state, inbox_store=inbox, include_runs=False) == []
    assert inbox.items[item.id].status == "handled"
    assert len(state._notification_log) == 1


def test_domain_notification_is_a_configurable_attention_kind():
    kind = resolve_kind("personal", "domain_alert")
    assert kind.key == "personal/domain_alert"
    assert kind.label == "Personal domain observation"
    assert kind.default_mode == "immediate"
    assert kind.default_severity == 2
    assert kind.attention is True
    assert kind.configurable is True
    assert (
        kind.production_owner == "gideon.workspace.capabilities.platform.domain_alerts"
    )
    assert kind_for_legacy("personal_domain_alert") == kind


@pytest.mark.asyncio
async def test_doctor_personal_capability_reads_real_stores_without_creating_them(home):
    probe = next(row for row in all_probes() if row.id == "personal.sources")
    assert probe.capability == "personal"
    assert not (home / "capabilities").exists()

    missing = await run_capability("personal", DoctorContext(home=home))
    assert missing["ok"] is False
    assert missing["probes"][0]["id"] == "personal.sources"
    assert {row["state"] for row in missing["probes"][0]["evidence"]["domains"]} == {
        "unconfigured"
    }
    assert not (home / "capabilities").exists()

    GoalStore(home / "capabilities/identity/goals.sqlite3")
    InterventionStore(home)
    ready = await run_capability("personal", DoctorContext(home=home))
    assert ready["ok"] is True
    domains = ready["probes"][0]["evidence"]["domains"]
    assert [(row["domain"], row["state"]) for row in domains] == [
        ("goals", "ready"),
        ("wellbeing", "ready"),
    ]

    (home / "capabilities/wellbeing.sqlite3").write_bytes(b"not a database")
    unavailable = await run_capability("personal", DoctorContext(home=home))
    assert unavailable["ok"] is False
    assert unavailable["probes"][0]["detail"] == "Personal domain source readiness"
    assert unavailable["probes"][0]["evidence"]["domains"][1]["state"] == "unavailable"

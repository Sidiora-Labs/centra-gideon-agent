import dataclasses
import json

import pytest

from gideon.automation.triggers.models import (
    FIRE_OUTCOMES,
    FireRecord,
    Issue,
    Trigger,
    TriggerState,
    fire_issues,
    parse_trigger,
    validate_spec,
)
from gideon.automation.triggers.store import TriggerStore


def test_trigger_projection_keeps_wire_order_and_shallow_copy_contract():
    nested = {"shared": []}
    trigger = Trigger(
        "ordered", "Ordered", "manual", spec={"nested": nested}, resource_slots=["gpu"]
    )
    projected = trigger.to_dict()
    assert list(projected) == [
        "id",
        "name",
        "kind",
        "enabled",
        "created_by",
        "author",
        "origin_harness",
        "spec",
        "gates",
        "capabilities",
        "workflow",
        "overlap",
        "session",
        "model_tier",
        "delivery",
        "failure_delivery",
        "retry",
        "failure_policy",
        "yield_to_user",
        "resource_slots",
        "skip_if_active",
        "catch_up",
        "expires_at",
        "next_fire_at",
        "last_run_id",
        "run_owner_pid",
        "run_count",
        "last_success_at",
        "last_failure_at",
        "last_fired_at",
        "park_retry_after",
        "last_alert_hash",
        "last_alert_at",
        "health_status",
        "last_error_summary",
        "state",
    ]
    field_names = [item.name for item in dataclasses.fields(Trigger)]
    assert field_names[-6:] == [
        "health_status",
        "last_error_summary",
        "state",
        "park_retry_after",
        "last_alert_hash",
        "last_alert_at",
    ]
    assert projected["spec"] is not trigger.spec
    assert projected["spec"]["nested"] is nested
    projected["spec"]["outer"] = "new"
    projected["resource_slots"].append("cpu")
    assert "outer" not in trigger.spec
    assert trigger.resource_slots == ["gpu"]


def test_validation_issues_keep_order_paths_messages_and_severity():
    trigger, issues = parse_trigger(
        {
            "kind": "manual",
            "enable": True,
            "zunknown": 1,
            "gates": {"debounce_seconds": 3},
            "workflow": {"resume": "not-an-object"},
            "overlap": "parallell",
            "state": "paussed",
        }
    )
    assert [item.to_dict() for item in issues] == [
        {
            "path": "enable",
            "message": "unknown trigger field 'enable'",
            "severity": "warning",
            "closest": "enabled",
        },
        {
            "path": "zunknown",
            "message": "unknown trigger field 'zunknown'",
            "severity": "warning",
            "closest": "",
        },
        {
            "path": "gates.debounce_seconds",
            "message": "unknown gate 'debounce_seconds'; it would be stored and never enforced",
            "severity": "warning",
            "closest": "debounce_secs",
        },
        {
            "path": "workflow.resume",
            "message": "a resume target must be an object with a run_id, not str",
            "severity": "error",
            "closest": "",
        },
        {
            "path": "id",
            "message": "a trigger needs an id",
            "severity": "error",
            "closest": "",
        },
        {
            "path": "name",
            "message": "a trigger needs a name",
            "severity": "error",
            "closest": "",
        },
        {
            "path": "overlap",
            "message": "unknown overlap policy 'parallell'",
            "severity": "warning",
            "closest": "parallel",
        },
        {
            "path": "state",
            "message": "unknown state 'paussed'",
            "severity": "warning",
            "closest": "paused",
        },
    ]
    assert trigger.enabled is False
    assert (trigger.state, trigger.overlap) == ("active", "skip")


@pytest.mark.parametrize(
    "spec,expected",
    [
        (
            {"kind": "adaptive"},
            ["spec.interval_secs_healthy", "spec.interval_secs_degraded"],
        ),
        (
            {
                "kind": "adaptive",
                "interval_secs_healthy": "bad",
                "interval_secs_degraded": -1,
            },
            ["spec.interval_secs_healthy", "spec.interval_secs_degraded"],
        ),
        (
            {
                "kind": "adaptive",
                "interval_secs_healthy": "900",
                "interval_secs_degraded": 30,
            },
            [],
        ),
        ({"kind": "sequence", "at": 0}, ["spec.at"]),
    ],
)
def test_clock_validation_reports_all_missing_cadences_in_field_order(spec, expected):
    issues = validate_spec("clock", spec)
    assert [item.path for item in issues] == expected
    assert all(item.severity == "error" for item in issues)


def test_authoring_conversions_preserve_opaque_origin_and_strict_flags():
    trigger, issues = parse_trigger(
        {
            "id": "  id  ",
            "name": " Name ",
            "kind": " MANUAL ",
            "author": " Alice ",
            "origin_harness": " Mixed-Case-Origin ",
            "enabled": "false",
            "yield_to_user": "true",
            "catch_up": 1,
            "resource_slots": [3, "gpu"],
            "run_count": "4",
            "park_retry_after": "12.5",
            "last_alert_at": "invalid",
            "workflow": [],
            "retry": None,
        }
    )
    assert issues == []
    assert (trigger.id, trigger.name, trigger.kind) == ("  id  ", " Name ", "manual")
    assert trigger.author == "alice"
    assert trigger.origin_harness == " Mixed-Case-Origin "
    assert trigger.enabled is True
    assert trigger.yield_to_user is False and trigger.catch_up is False
    assert trigger.resource_slots == ["3", "gpu"]
    assert (trigger.run_count, trigger.park_retry_after, trigger.last_alert_at) == (
        4,
        12.5,
        0.0,
    )
    assert trigger.workflow == {} and trigger.retry == {}


@pytest.mark.parametrize("state", [state.value for state in TriggerState])
@pytest.mark.parametrize("kind", ["manual", "event"])
@pytest.mark.parametrize("enabled", [False, True])
def test_automatic_fire_eligibility_uses_kind_lifecycle_and_enable(
    state, kind, enabled
):
    trigger = Trigger("eligibility", "Eligibility", kind, enabled=enabled, state=state)
    assert trigger.fires_automatically is (
        enabled and kind == "event" and state == "active"
    )


@pytest.mark.parametrize("outcome", FIRE_OUTCOMES)
@pytest.mark.parametrize("mutated", [False, True])
def test_fire_projection_preserves_materiality_and_failure_semantics(
    outcome, mutated, tmp_path
):
    record = FireRecord(
        "fire",
        "trigger",
        outcome,
        reason="recorded reason",
        mutated=mutated,
        counters={"items": 2},
        scheduled_for="2026-09-01T00:00:00Z",
        incomplete=True,
        acted_on=True,
        dismissed=False,
    )
    path = tmp_path / "fire.json"
    path.write_text(json.dumps(record.to_dict()))
    restored = FireRecord.from_dict(json.loads(path.read_text()))
    assert restored == record
    assert restored.productive is (mutated and outcome in {"ran", "ran_late"})
    assert restored.counts_toward_autopause is (outcome == "failed")
    assert fire_issues(restored) == []


def test_unknown_fire_values_degrade_without_promoting_success():
    record = FireRecord.from_dict(
        {
            "outcome": "unrecognized",
            "weight": "unrecognized",
            "duration_secs": "bad",
            "counters": [],
            "mutated": 1,
            "incomplete": "true",
            "acted_on": True,
        }
    )
    assert (record.outcome, record.weight, record.duration_secs, record.counters) == (
        "failed",
        "ledger",
        0.0,
        {},
    )
    assert (
        record.mutated is False
        and record.incomplete is False
        and record.acted_on is True
    )
    assert record.productive is False and record.counts_toward_autopause is True
    assert [item.path for item in fire_issues(record)] == ["reason"]
    assert list(Issue("x", "message").to_dict()) == [
        "path",
        "message",
        "severity",
        "closest",
    ]


def test_store_retains_broken_rows_and_round_trips_runtime_rollups(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    good = Trigger(
        "good",
        "Good",
        "manual",
        author="alice",
        origin_harness="origin-1",
        state="parked",
        park_retry_after=100.5,
        last_alert_hash="alert-hash",
        last_alert_at=99.5,
        last_fired_at="2026-09-01T00:00:00Z",
        run_count=4,
        health_status="parked",
        last_error_summary="temporarily busy",
    )
    store.save_all([good])
    document = json.loads(store.path.read_text())
    document["triggers"].append(
        {
            "id": "bad",
            "name": "Bad",
            "kind": "event",
            "spec": {"source": "memory", "agent_scope": []},
        }
    )
    store.path.write_text(json.dumps(document))
    loaded = TriggerStore(base_dir=tmp_path).load()
    assert [row.trigger.id for row in loaded] == ["good", "bad"]
    assert loaded[0].trigger == good
    assert loaded[1].trigger.enabled is False
    assert [issue.path for issue in loaded[1].errors] == ["spec.agent_scope"]
    assert store.set_enabled("bad", True) is None

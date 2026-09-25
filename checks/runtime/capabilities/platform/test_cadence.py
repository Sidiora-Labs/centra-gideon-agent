import json
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.service import TickPass, next_after_completion, to_epoch
from gideon.automation.triggers.store import TriggerStore
from gideon.engine.trigger_outcomes import FireResult
from gideon.integrations.mcp_core import (
    reset_current_session_key,
    set_current_session_key,
)
from gideon.interfaces.dashboard.handlers.capabilities_cadence import register
from gideon.interfaces.dashboard.token_auth import (
    generate_token,
    token_auth_middleware,
    use_ephemeral_secret,
)
from gideon.sdk.tool import ToolResult
from gideon.workspace.capabilities.platform import cadence as c
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = "/api/capabilities/platform/cadence"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)
    use_ephemeral_secret()
    return tmp_path


def trigger(identifier="audit-one", interval=60):
    value = Trigger(
        id=identifier,
        name=identifier,
        kind="clock",
        spec={"kind": "interval", "interval_secs": interval},
        workflow={"ref": "audit-workflow"},
    )
    TriggerStore().upsert(value)
    assert TriggerStore().get(value.id).ok
    return value


def opt(value, task_class="structural_audit", enabled=True):
    return c.mutate(
        value.id,
        {
            "revision": c.document()["revision"],
            "enabled": enabled,
            "task_class": task_class,
        },
    )


async def evidence(value, successes, *, now, offset=0, error=None):
    journal = ExecutionJournal(c._root())
    for index, success in enumerate(successes):
        outcome = FireResult.read(
            ToolResult(
                success=success, error="" if success else "Task verification failed"
            ),
            error,
        )
        stamp = now - len(successes) + index - offset
        await journal.append(
            ExecutionRecord(
                run_id=f"{value.id}-{offset}-{index}",
                job_id=value.id,
                trigger=outcome.exit_type,
                started_at=stamp,
                finished_at=stamp,
                status="success" if outcome.exit_type == "ok" else "failure",
                error=outcome.exception_text,
            )
        )


@pytest.mark.asyncio
async def test_real_typed_task_failures_slow_actual_interval_reschedule(home):
    value = trigger()
    now = time.time()
    await evidence(value, [False] * 5, now=now)
    assert c.projection(value, now=now)["reason"] == "not_opted_in"
    assert next_after_completion(value, completed_at=now, now=now) == now + 60
    opt(value)
    before = (home / "cron-history" / (value.id + ".jsonl")).read_bytes()
    result = c.projection(value, now=now)
    assert result["samples"] == 5
    assert result["successes"] == 0
    assert result["multiplier"] == 4
    assert result["effective_interval"] == 240
    assert result["confidence"] == "observed"
    assert result["reason"] == "low_execution_success"
    assert all(not row["success"] for row in result["evidence"])
    assert (
        next_after_completion(value, completed_at=now, now=now, base_dir=home)
        == now + 240
    )
    tick = TickPass(TriggerStore(home), now, True, False, home)
    tick.advance(value)
    assert to_epoch(
        TriggerStore(home).get(value.id).trigger.next_fire_at
    ) == pytest.approx(now + 240)
    assert tick.result.rescheduled == [value.id]
    assert (home / "cron-history" / (value.id + ".jsonl")).read_bytes() == before
    assert TriggerStore(home).get(value.id).trigger.spec["interval_secs"] == 60


@pytest.mark.asyncio
async def test_same_class_pooling_and_separate_class_isolation(home):
    now = time.time()
    first, peer, other = trigger("first"), trigger("peer"), trigger("other")
    opt(first)
    opt(peer)
    opt(other, "different_audit")
    await evidence(first, [False, False], now=now)
    await evidence(peer, [False, False, False], now=now, offset=2)
    await evidence(other, [True] * 8, now=now)
    result = c.projection(first, now=now)
    assert result["samples"] == 5
    assert result["effective_interval"] == 240
    assert {row["trigger_id"] for row in result["evidence"]} == {"first", "peer"}
    independent = c.projection(other, now=now)
    assert independent["samples"] == 8
    assert independent["effective_interval"] == 60
    assert independent["reason"] == "recent_recovery"
    assert c.view(now=now)["revision"] == 3
    assert len(c.view(now=now)["triggers"]) == 3


@pytest.mark.asyncio
async def test_actual_transport_error_classification_and_unknown_manual_exclusion(home):
    value = trigger()
    now = time.time()
    opt(value)
    await evidence(value, [False] * 4, now=now)
    await evidence(
        value,
        [False] * 4,
        now=now,
        offset=20,
        error=ConnectionError("transport unavailable"),
    )
    journal = ExecutionJournal(home)
    for index, kind in enumerate(
        (
            "auth_unavailable",
            "config_error",
            "manual",
            "scheduled",
            "deferred",
            "skipped_budget",
            "unknown",
        )
    ):
        await journal.append(
            ExecutionRecord(
                run_id=f"excluded-{index}",
                job_id=value.id,
                trigger=kind,
                status="failure",
                started_at=now - 1,
                finished_at=now - 1,
            )
        )
    result = c.projection(value, now=now)
    assert result["samples"] == 4
    assert result["excluded"] == 11
    assert result["reason"] == "insufficient_samples"
    assert result["effective_interval"] == 60
    rows, count = await journal.list_for_job(value.id, limit=100)
    assert count == 15
    assert any(row["trigger"] == "transport_unavailable" for row in rows)
    assert sum(c.outcome(row) is None for row in rows) == 11


@pytest.mark.asyncio
async def test_three_recent_successes_and_idle_day_restore_probe(home):
    value = trigger()
    now = time.time()
    opt(value)
    await evidence(value, [False] * 8, now=now, offset=10)
    assert c.projection(value, now=now)["effective_interval"] == 240
    stale = c.projection(value, now=now + 86401)
    assert stale["reason"] == "idle_grace_probe"
    assert stale["effective_interval"] == 60
    assert stale["samples"] == 8
    await evidence(value, [True] * 3, now=now)
    recovered = c.projection(value, now=now)
    assert recovered["reason"] == "recent_recovery"
    assert recovered["effective_interval"] == 60
    assert recovered["samples"] == 11
    assert recovered["successes"] == 3
    assert next_after_completion(value, completed_at=now, now=now) == now + 60


@pytest.mark.asyncio
async def test_bounded_sample_window_interval_cap_and_no_acceleration(home):
    now = time.time()
    value = trigger(interval=30000)
    opt(value)
    await evidence(value, [False] * 25, now=now)
    result = c.projection(value, now=now)
    assert result["samples"] == 20
    assert len(result["evidence"]) == 20
    assert result["effective_interval"] == 86400
    assert result["multiplier"] == pytest.approx(2.88)
    value.spec["interval_secs"] = 100000
    TriggerStore().upsert(value)
    result = c.projection(value, now=now)
    assert result["effective_interval"] == 100000
    assert result["multiplier"] == 1


@pytest.mark.asyncio
async def test_binding_change_and_optout_preserve_configured_cadence(home):
    now = time.time()
    value = trigger()
    opt(value)
    await evidence(value, [False] * 5, now=now)
    assert c.projection(value, now=now)["effective_interval"] == 240
    changed = TriggerStore().get(value.id).trigger
    changed.workflow = {"ref": "different-workflow"}
    TriggerStore().upsert(changed)
    projection = c.projection(changed, now=now)
    assert projection["reason"] == "workflow_binding_changed"
    assert projection["samples"] == 0
    assert projection["effective_interval"] == 60
    opt(changed, enabled=False)
    assert c.document()["policies"] == {}
    assert c.projection(changed, now=now)["reason"] == "not_opted_in"
    assert next_after_completion(changed, completed_at=now, now=now) == now + 60
    assert c.document()["revision"] == 2


def test_policy_validation_revisions_and_unrelated_manual_trigger(home):
    value = trigger()
    response = opt(value)
    assert response["revision"] == 1
    with pytest.raises(ValueError, match="changed"):
        c.mutate(value.id, {"revision": 0, "enabled": False, "task_class": ""})
    with pytest.raises(ValueError, match="lowercase"):
        c.mutate(value.id, {"revision": 1, "enabled": True, "task_class": "../escape"})
    with pytest.raises(ValueError, match="required"):
        c.mutate(value.id, {"revision": 1, "enabled": "yes", "task_class": "audit"})
    manual = Trigger(
        id="manual-one",
        name="Manual action",
        kind="manual",
        workflow={"ref": "audit-workflow"},
    )
    TriggerStore().upsert(manual)
    with pytest.raises(ValueError, match="interval"):
        c.mutate(manual.id, {"revision": 1, "enabled": True, "task_class": "audit"})
    assert [row["trigger_id"] for row in c.view()["triggers"]] == [value.id]
    assert TriggerStore().get(manual.id).trigger.spec == {}
    assert c.document()["revision"] == 1
    c._path().write_text("{broken")
    assert c.projection(value)["reason"] == "evidence_unavailable"
    assert c.effective_interval(value, now=time.time()) == 60


@pytest.mark.asyncio
async def test_signed_http_and_actual_native_policy_mutations(home):
    value = trigger()
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        response = await client.get(PREFIX, params={"token": generate_token("owner")})
        assert response.status == 200
        assert (await response.json())["revision"] == 0
        response = await client.put(
            PREFIX + "/" + value.id,
            json={"revision": 0, "enabled": True, "task_class": "audit"},
        )
        assert response.status == 200
        assert (await response.json())["triggers"][0]["task_class"] == "audit"
        assert (
            await client.put(
                PREFIX + "/" + value.id,
                json={"revision": 0, "enabled": False, "task_class": ""},
            )
        ).status == 409
        provider = create_provider()
        read = await provider.invoke("platform_task_cadence", {})
        assert read.success
        assert json.loads(read.output)["revision"] == 1
        token = set_current_session_key("agent:cadence")
        try:
            result = await provider.invoke(
                "platform_task_cadence_set",
                {
                    "trigger_id": value.id,
                    "revision": 1,
                    "enabled": False,
                    "task_class": "",
                },
            )
            assert result.success
            assert json.loads(result.output)["revision"] == 2
        finally:
            reset_current_session_key(token)
        token = set_current_session_key("")
        try:
            denied = await provider.invoke(
                "platform_task_cadence_set",
                {
                    "trigger_id": value.id,
                    "revision": 2,
                    "enabled": True,
                    "task_class": "audit",
                },
            )
            assert not denied.success
        finally:
            reset_current_session_key(token)
        tools = await provider.list_tools()
        tool = next(item for item in tools if item.name == "platform_task_cadence_set")
        assert tool.requires_approval
        manifest = json.loads(
            Path(
                "runtime/gideon/extensions/apps/native/gideon-platform/app.json"
            ).read_text()
        )
        assert tool.name in manifest["provider"]["capabilities"]
        assert c.document()["policies"] == {}

"""Process-owner recovery for trigger claims (req.29)."""

from __future__ import annotations

import logging
import os
import time

import pytest

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers import claims
from gideon.automation.triggers.models import Trigger, TriggerHealth
from gideon.automation.triggers.scheduling import Claim
from gideon.automation.triggers.store import TriggerStore
from gideon.engine import automation_boot
from gideon.engine.automation_boot import AutomationBoot
from gideon.engine.trigger_dispatch import TriggerDispatch

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _trigger(store: TriggerStore, *, owner_pid: int = 0) -> Trigger:
    trigger = Trigger(
        id="clock:owned",
        name="Owned",
        kind="clock",
        run_owner_pid=owner_pid,
    )
    store.upsert(trigger)
    return trigger


def _claim(home, trigger_id: str = "clock:owned") -> None:
    claims.write_claim(
        Claim(trigger_id=trigger_id, holder="tick:1", claimed_at=time.time()),
        base_dir=home,
    )


def _boot(home) -> AutomationBoot:
    return AutomationBoot(None, home=lambda: home, logger=logging.getLogger("task29"))


def test_trigger_dispatch_stamps_the_run_owner_in_the_store(tmp_path, monkeypatch):
    store = TriggerStore(base_dir=tmp_path)
    trigger = _trigger(store)
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    dispatch = TriggerDispatch(
        None, trigger, {}, "trigger.fired", logging.getLogger("t")
    )

    dispatch._stamp_run_owner()

    assert trigger.run_owner_pid == os.getpid()
    assert store.get(trigger.id).trigger.run_owner_pid == os.getpid()


async def test_boot_terminalizes_a_provably_dead_owner_claim(tmp_path, monkeypatch):
    store = TriggerStore(base_dir=tmp_path)
    _trigger(store, owner_pid=912_345)
    _claim(tmp_path)

    def gone(pid: int, signal: int) -> None:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(automation_boot.os, "kill", gone)

    assert await _boot(tmp_path).recover_interrupted(store) == ["clock:owned"]
    assert claims.read_claim("clock:owned", base_dir=tmp_path) is None
    saved = store.get("clock:owned").trigger
    assert saved.run_owner_pid == 0
    assert saved.health_status == TriggerHealth.DEGRADED.value
    assert "interrupted" in saved.last_error_summary
    rows, total = await ExecutionJournal(tmp_path).list_for_job("clock:owned")
    assert total == 1
    assert rows[0]["status"] == "failure"
    assert "owner PID 912345" in rows[0]["error"]


async def test_boot_preserves_a_claim_owned_by_a_live_process(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    _trigger(store, owner_pid=os.getpid())
    _claim(tmp_path)

    assert await _boot(tmp_path).recover_interrupted(store) == []
    assert claims.read_claim("clock:owned", base_dir=tmp_path) is not None
    saved = store.get("clock:owned").trigger
    assert saved.run_owner_pid == os.getpid()
    assert saved.health_status == TriggerHealth.OK.value
    assert await ExecutionJournal(tmp_path).list_for_job("clock:owned") == ([], 0)


async def test_boot_preserves_a_claim_when_owner_liveness_is_unknown(
    tmp_path, monkeypatch
):
    store = TriggerStore(base_dir=tmp_path)
    _trigger(store, owner_pid=912_346)
    _claim(tmp_path)

    def denied(pid: int, signal: int) -> None:
        raise PermissionError(pid)

    monkeypatch.setattr(automation_boot.os, "kill", denied)

    assert await _boot(tmp_path).recover_interrupted(store) == []
    assert claims.read_claim("clock:owned", base_dir=tmp_path) is not None
    assert store.get("clock:owned").trigger.health_status == TriggerHealth.OK.value


async def test_boot_preserves_an_unstamped_claim(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    _trigger(store)
    _claim(tmp_path)

    assert await _boot(tmp_path).recover_interrupted(store) == []
    assert claims.read_claim("clock:owned", base_dir=tmp_path) is not None

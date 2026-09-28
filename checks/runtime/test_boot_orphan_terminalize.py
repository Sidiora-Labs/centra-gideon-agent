from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time

from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers import claims
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.scheduling import Claim
from gideon.automation.triggers.store import TriggerStore
from gideon.engine.automation_boot import AutomationBoot


def test_boot_only_terminalizes_claim_with_provably_dead_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    store = TriggerStore(base_dir=tmp_path)
    dead_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    dead_process.terminate()
    dead_process.wait(timeout=5)
    dead = Trigger(
        id="clock:dead-owner",
        name="Dead owner",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 900},
        run_owner_pid=dead_process.pid,
    )
    store.upsert(dead)
    claims.write_claim(Claim(dead.id, "dead-owner", time.time()), base_dir=tmp_path)
    live_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        live = Trigger(
            id="clock:live-owner",
            name="Live owner",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 900},
            run_owner_pid=live_process.pid,
        )
        store.upsert(live)
        claims.write_claim(Claim(live.id, "live-owner", time.time()), base_dir=tmp_path)

        boot = AutomationBoot(None, home=lambda: tmp_path, logger=logging.getLogger(__name__))
        interrupted = asyncio.run(boot.recover_interrupted(store))

        assert interrupted == [dead.id]
        recovered = store.get(dead.id).trigger
        assert recovered.run_owner_pid == 0
        assert recovered.last_run_id.startswith("interrupted-")
        assert not claims.is_running(dead.id, base_dir=tmp_path)
        records, count = asyncio.run(ExecutionJournal(tmp_path).list_for_job(dead.id))
        assert count == 1
        assert len(records) == 1
        assert records[0]["status"] == "interrupted"

        assert store.get(live.id).trigger.run_owner_pid == live_process.pid
        assert claims.is_running(live.id, base_dir=tmp_path)
    finally:
        live_process.terminate()
        live_process.wait(timeout=5)

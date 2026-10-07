"""Execution history retains recent activity through writers, boot rotation and restart."""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
from gideon.core.bounded_log import in_time_order
from gideon.engine.automation_boot import AutomationBoot


def row(i, *, job="timer", status="success", stamp=None):
    return ExecutionRecord(
        run_id=str(i),
        job_id=job,
        started_at=i if stamp is None else stamp,
        status=status,
    ).to_dict()


def write_history(home, rows, index=None):
    folder = home / "cron-history"
    folder.mkdir(exist_ok=True)
    (folder / "timer.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (folder / "_index.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (rows if index is None else index))
    )


def test_real_writer_keeps_own_times_and_suppression_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    # A restored old tail must not evict the newest runs at the front.
    rows = [row(i) for i in range(1000, 1100)] + [
        row(i, status="skipped_gate") for i in range(40)
    ]
    write_history(tmp_path, rows)
    journal = ExecutionJournal(tmp_path)
    asyncio.run(
        journal.append(
            ExecutionRecord(run_id="current", job_id="timer", started_at=2000)
        )
    )
    page, total = journal.list_for_job_sync("timer", limit=200)
    assert total == 100
    assert page[0]["run_id"] == "current"
    assert len([r for r in page if r["status"] == "skipped_gate"]) == 25
    assert {r["run_id"] for r in page if r["status"] == "skipped_gate"} == {
        str(i) for i in range(15, 40)
    }
    assert {r["run_id"] for r in page if r["status"] == "success"} == {
        "current",
        *(str(i) for i in range(1026, 1100)),
    }


def test_actual_boot_rotation_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    rows = [row(i) for i in range(1000, 1100)] + [row(i) for i in range(10)]
    index = [row(i, job=f"job-{i % 25}") for i in range(10000, 12000)] + [
        row(i, job=f"job-{i % 25}") for i in range(10)
    ]
    write_history(tmp_path, rows, index)
    asyncio.run(
        AutomationBoot(
            None, home=lambda: tmp_path, logger=logging.getLogger("retention")
        ).rotate()
    )
    journal = ExecutionJournal(tmp_path)
    page, total = journal.list_for_job_sync("timer", limit=200)
    assert total == 100 and [r["started_at"] for r in page] == list(
        range(1099, 999, -1)
    )
    records, total = asyncio.run(journal.list_all(limit=3000))
    assert total == 2000 and [r["started_at"] for r in records] == list(
        range(11999, 9999, -1)
    )
    code = """import asyncio,os
from pathlib import Path
from gideon.automation.schedule_history import ExecutionJournal
j=ExecutionJournal(Path(os.environ['GIDEON_HOME']))
p,total=j.list_for_job_sync('timer',limit=200)
assert total==100 and p[0]['started_at']==1099 and p[-1]['started_at']==1000
p,total=asyncio.run(j.list_all(limit=3000))
assert total==2000 and p[0]['started_at']==11999 and p[-1]['started_at']==10000
"""
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_two_actual_writers_do_not_lose_recent_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    write_history(tmp_path, [row(i) for i in range(90)])
    code = """import asyncio,os,sys
from pathlib import Path
from gideon.automation.schedule_history import ExecutionJournal,ExecutionRecord
async def main():
 j=ExecutionJournal(Path(os.environ['GIDEON_HOME']))
 for i in range(20):
  await j.append(ExecutionRecord(run_id=f'{sys.argv[1]}-{i}',job_id='timer',started_at=1000+i))
asyncio.run(main())
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, name],
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ["first", "second"]
    ]
    for child in children:
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, out + err
    page, total = ExecutionJournal(tmp_path).list_for_job_sync("timer", limit=200)
    assert total == 100
    assert {f"{name}-{i}" for name in ["first", "second"] for i in range(20)} <= {
        r["run_id"] for r in page
    }


def test_invalid_times_and_ties_remain_deterministic():
    rows = [
        {"id": "late", "at": 2},
        {"id": "invalid", "at": True},
        {"id": "tie-first", "at": 1},
        {"id": "tie-last", "at": "1970-01-01T00:00:01Z"},
        {"id": "invalid-last", "at": "unreadable"},
    ]
    assert [r["id"] for r in in_time_order(rows, at="at")] == [
        "invalid",
        "invalid-last",
        "tie-first",
        "tie-last",
        "late",
    ]

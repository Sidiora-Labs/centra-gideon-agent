from dataclasses import replace
from datetime import datetime, timezone

import pytest

from gideon.automation.schedule import (
    ScheduleDefinition,
    ScheduleJob,
    _action_from_record,
    build_schedule_session_context,
    compute_next_run_ts,
    format_schedule,
    make_agent_action,
    make_command_action,
    make_script_action,
    normalize_action,
)
from gideon.automation.schedule_script import ScriptLaunchFiles, run_script_sandboxed
from gideon.engine import gateway_base


def test_session_context_accumulates_only_for_persistent_jobs():
    job = ScheduleJob(
        id="abc123",
        name="report",
        action=make_agent_action("run now"),
        acked_items=["first", "second"],
        last_result="previous",
    )
    key, prompt = build_schedule_session_context(job)
    assert key == "cron:abc123"
    assert (
        prompt.index("- first")
        < prompt.index("- second")
        < prompt.index("previous")
        < prompt.index("run now")
    )
    isolated = replace(job, persistent_session=False)
    first_key, first_prompt = build_schedule_session_context(isolated)
    second_key, second_prompt = build_schedule_session_context(isolated)
    assert first_key != second_key
    assert first_key.startswith("cron:abc123:")
    assert first_prompt == second_prompt
    assert "previous" not in first_prompt and "- first" not in first_prompt
    assert "run now" in first_prompt


def test_action_precedence_and_projection():
    stored = make_agent_action("prompt", "agent", "model", "auto")
    assert _action_from_record({"action": stored, "script": "ignored.py:run"}) is stored
    script = _action_from_record(
        {"script": "s.py:run", "command": "ignored", "zt_timeout": "9"}
    )
    job = ScheduleJob(id="a", name="n", action=script)
    assert (job.exec_mode, job.script, job.command, job.message, job.zt_timeout) == (
        "script",
        "s.py:run",
        "",
        "",
        9,
    )
    assert _action_from_record({"command": "echo yes"}) == make_command_action(
        "echo yes"
    )
    agent = replace(job, action=stored)
    assert (agent.message, agent.agent_id, agent.model, agent.approval_mode) == (
        "prompt",
        "agent",
        "model",
        "auto",
    )
    with pytest.raises(ValueError, match="approval_mode"):
        normalize_action(make_agent_action(approval_mode="ask"))
    with pytest.raises(ValueError, match="config"):
        normalize_action({"provider": "other", "config": [1]})


def test_clock_boundaries_and_dst():
    job = ScheduleJob(
        id="a",
        name="n",
        created_ts=100,
        schedule=ScheduleDefinition("every", every_secs=60),
    )
    assert compute_next_run_ts(job, 120) == 160
    assert compute_next_run_ts(job, 170) == 170
    assert compute_next_run_ts(replace(job, last_run_ts=150), 170) == 210
    assert compute_next_run_ts(replace(job, enabled=False), 170) is None
    once = replace(job, schedule=ScheduleDefinition("at", at_ts=170))
    assert compute_next_run_ts(once, 169) == 170
    assert compute_next_run_ts(once, 170) is None
    cron = replace(
        job,
        timezone="America/New_York",
        schedule=ScheduleDefinition("cron", cron_expr="30 8 * * *"),
    )
    now = datetime(2026, 3, 7, 15, tzinfo=timezone.utc).timestamp()
    expected = datetime(2026, 3, 8, 12, 30, tzinfo=timezone.utc).timestamp()
    assert compute_next_run_ts(cron, now) == expected
    assert compute_next_run_ts(replace(cron, timezone="invalid/zone"), now) is None
    assert format_schedule(ScheduleDefinition("every", every_secs=7199)) == "every 1h"
    assert format_schedule(ScheduleDefinition("every", every_secs=59)) == "every 59s"


def test_actual_worker_preserves_control_receipts_and_consumes_private_input(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv(gateway_base.PORT_ENV, "7777")
    crons = tmp_path / "crons"
    crons.mkdir()
    script = crons / "receipt.py"
    script.write_text(
        "import os, sys\n"
        "from gideon.automation.schedule_script import Report\n"
        "def run(ctx):\n"
        "    assert not os.path.exists(sys.argv[1])\n"
        "    print('user output before receipt')\n"
        "    raise Report(ctx.message + ':reported')\n",
        encoding="utf-8",
    )
    result = run_script_sandboxed(f"{script}:run", "receipt", "input", timeout=30)
    assert result == {"status": "report", "message": "input:reported"}
    assert (
        normalize_action(make_script_action(f"{script}:run"))["provider"]
        == "run-script"
    )
    missing = run_script_sandboxed(f"{script}:absent", "receipt", "", timeout=30)
    assert missing == {"status": "error", "error": "function 'absent' not found"}


def test_launch_files_are_owned_during_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    configuration = dict(
        script_path=str(tmp_path / "unused.py"), func="run", secret="private"
    )
    with pytest.raises(RuntimeError, match="caller stopped"):
        with ScriptLaunchFiles.stage(configuration) as command:
            from pathlib import Path

            private_input, launcher = Path(command[-1]), Path(command[-2])
            assert private_input.stat().st_mode & 0o777 == 0o600
            assert "private" in private_input.read_text()
            assert launcher.is_file()
            raise RuntimeError("caller stopped")
    assert not private_input.exists()
    assert not launcher.exists()

"""`cron list` renders automations from the unified TRIGGER STORE.

🔴 REWRITTEN for core's S112, which deleted `ScheduleService`. These tests drove that service by
assigning `cron_service._jobs = [...]` — poking a private list on a class whose file the store
replaced. Worse, they passed the whole time the command was BROKEN: the service read `crons.json`,
which nothing has written since core's S108, so a real user's `cron list` showed an empty list and
remove/pause/resume answered "not found" for every live id.

The contract under test is unchanged — the relative next-run rendering, and that a job's message is
redacted before it reaches Slack. Both now go through a real `TriggerStore`.
"""

import re
import time
from unittest.mock import patch

import pytest

from gideon.sdk.channel import Trigger, TriggerStore
from slack_desk_runtime.handler import _handle_cron_command, _relative_next_run


@pytest.fixture()
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _seed(store, *, enabled: bool = True, message: str = "do something important",
          next_fire_at: str = "", trigger_id: str = "clock:test-job") -> Trigger:
    trigger = Trigger(
        id=trigger_id,
        name="test-job",
        kind="clock",
        enabled=enabled,
        spec={"kind": "cron", "expr": "0 13 * * *"},
        workflow={"inline": {"provider": "invoke-agent", "config": {"task_template": message}}},
        next_fire_at=next_fire_at,
    )
    store.upsert(trigger)
    return trigger


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class TestRelativeNextRun:
    """The pure renderer, extracted so each band is testable without a Slack round-trip."""

    def test_days(self) -> None:
        now = 1_700_000_000.0
        assert _relative_next_run(now + 3 * 86400 + 7200, now) == " | ⏭ in 3d 2h"

    def test_hours(self) -> None:
        now = 1_700_000_000.0
        assert re.search(r"⏭ in \d+h \d+m", _relative_next_run(now + 7200, now))

    def test_minutes(self) -> None:
        now = 1_700_000_000.0
        assert _relative_next_run(now + 1800, now) == " | ⏭ in 30m"

    def test_less_than_one_minute(self) -> None:
        now = 1_700_000_000.0
        assert _relative_next_run(now + 30, now) == " | ⏭ in <1m"

    def test_past_due_reads_now(self) -> None:
        """A missed fire reads "now", not a negative duration."""
        now = 1_700_000_000.0
        assert _relative_next_run(now - 5, now) == " | ⏭ now"

    def test_no_next_run_renders_nothing(self) -> None:
        assert _relative_next_run(None, 1_700_000_000.0) == ""


class TestHandleCronList:
    def test_it_lists_an_armed_automation_with_its_next_run(self, store) -> None:
        _seed(store, next_fire_at=_iso(time.time() + 7200))
        result = _handle_cron_command("cron list", store, "C123", "t123")
        assert result is not None
        assert "test-job" in result
        assert re.search(r"⏭ in \d+h", result)

    def test_an_unarmed_automation_shows_no_next_run(self, store) -> None:
        """A disabled row is never armed, so there is no fire to render."""
        _seed(store, enabled=False)
        result = _handle_cron_command("cron list", store, "C123", "t123")
        assert result is not None
        assert "⏸️" in result
        assert "⏭" not in result

    def test_an_empty_store_says_so(self, store) -> None:
        assert _handle_cron_command("cron list", store, "C123", "t123") == (
            "No automations scheduled."
        )

    def test_a_broken_row_is_listed_with_its_reason(self, store) -> None:
        """🔴 Better than the legacy list, which could not represent a broken row at all — silently
        omitting an automation the user created is how "where did my automation go" happens."""
        store.upsert(Trigger(id="clock:broken", name="broken", kind="clock", spec={}))
        result = _handle_cron_command("cron list", store, "C123", "t123")
        assert result is not None
        assert "⚠️" in result
        assert "clock:broken" in result

    def test_the_message_is_redacted(self, store) -> None:
        _seed(store, message="token=AKIAIOSFODNN7EXAMPLE")
        with (
            patch(
                "slack_desk_runtime.handler.redact_exfiltration_urls",
                return_value=("[URL_REDACTED]", True),
            ) as mock_url,
            patch(
                "slack_desk_runtime.handler.redact_credentials", return_value=("[REDACTED]", True)
            ) as mock_cred,
        ):
            result = _handle_cron_command("cron list", store, "C123", "t123")
        mock_url.assert_called_once_with("token=AKIAIOSFODNN7EXAMPLE")
        mock_cred.assert_called_once_with("[URL_REDACTED]")
        assert result is not None
        assert "[REDACTED]" in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result


class TestHandleCronMutations:
    """remove / pause / resume — the halves that answered "not found" for every real id."""

    def test_pause_then_resume(self, store) -> None:
        _seed(store)
        assert "Paused" in (
            _handle_cron_command("cron pause clock:test-job", store, "C", "t") or ""
        )
        assert store.get("clock:test-job").trigger.enabled is False
        assert "Resumed" in (
            _handle_cron_command("cron resume clock:test-job", store, "C", "t") or ""
        )
        assert store.get("clock:test-job").trigger.enabled is True

    def test_remove(self, store) -> None:
        _seed(store)
        assert "Removed" in (
            _handle_cron_command("cron remove clock:test-job", store, "C", "t") or ""
        )
        assert store.get("clock:test-job") is None

    def test_an_unknown_id_is_reported(self, store) -> None:
        assert "not found" in (_handle_cron_command("cron remove nope", store, "C", "t") or "")
        assert "not found" in (_handle_cron_command("cron pause nope", store, "C", "t") or "")

    def test_resuming_a_broken_row_names_the_parse_error(self, store) -> None:
        """The store REFUSES to enable a row that failed to parse and says why — strictly more
        useful than "not found", and the row does exist, so that message was wrong as well."""
        store.upsert(
            Trigger(id="clock:broken", name="broken", kind="clock", spec={}, enabled=False)
        )
        result = _handle_cron_command("cron resume clock:broken", store, "C", "t") or ""
        assert "parse error" in result
        assert store.get("clock:broken").trigger.enabled is False

    def test_remove_all_is_scoped_to_agent_created_automations(self, store) -> None:
        """🔴 A REAL FIX, not a port. The old `cron remove all` removed EVERYTHING the scheduler
        held, including the automations the user built by hand — from a chat message, with no
        confirmation. It is now scoped to `created_by="agent"`, matching what core's
        `automation_delete_all` enforces."""
        _seed(store, trigger_id="clock:mine")
        store.upsert(
            Trigger(
                id="clock:theirs",
                name="agent made this",
                kind="clock",
                created_by="agent",
                spec={"kind": "cron", "expr": "0 9 * * *"},
            )
        )

        result = _handle_cron_command("cron remove all", store, "C", "t") or ""

        assert "Removed 1" in result
        assert store.get("clock:theirs") is None
        assert store.get("clock:mine") is not None, "the user's own automation must survive"

    def test_remove_all_with_nothing_agent_created(self, store) -> None:
        _seed(store, trigger_id="clock:mine")
        result = _handle_cron_command("cron remove all", store, "C", "t") or ""
        assert "No agent-created automations" in result
        assert store.get("clock:mine") is not None


def test_a_non_cron_message_is_not_intercepted(store) -> None:
    """The handler must return None for ordinary chat, or it would swallow the user's message."""
    assert _handle_cron_command("what is the weather", store, "C", "t") is None
    assert _handle_cron_command("cron", store, "C", "t") is None

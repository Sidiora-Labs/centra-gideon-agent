"""An unattended trigger failure keeps its WHAT/WHY/FIX envelope (req.43).

The envelope exists and the providers build it — `net_fetch_provider` and `browse_provider` return
`ActionResult.agent_error`, and `provider_failure()` wraps a raise from any provider that never
heard of it. The cron dispatch seam is the one that threw it away: it recorded
`f"{type(exc).__name__}: {exc}"` cut at 200 characters and notified with the same, so the only
failure nobody watches happen — an unattended one — was the only failure with no fix attached.

What these pin: the run-history row, its `FireRecord` projection, and the failure notification all
carry the same envelope, bounded PER LINE rather than by a tail cut of the rendering. The
load-bearing test is `test_a_long_explanation_keeps_all_three_lines` — a whole-envelope cut looks
identical to a bounded one until the explanation is long, and then it silently eats FIX.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import gideon.integrations.action_providers as AP
from gideon.automation.schedule_history import ExecutionJournal
from gideon.automation.triggers.history import schedule_run_to_record
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.core.errors import ENVELOPE_FIELD_CAP, AgentError
from gideon.engine.gateway import RuntimeCoordinator
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.net_fetch_provider import (
    NetFetchActionProvider,
)

TRIGGER_ID = "clock:unattended"

LONG = AgentError(
    code="ERR_ACTION_PROVIDER_FAILED",
    what="w" * 400,
    why="y" * 400,
    fix="f" * 400,
)


class _State:
    """A dashboard state that records `notify` kwargs — the shape `deliver` calls it with."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify(self, *, kind, title, body, meta=None):
        self.sent.append(
            {"kind": kind, "title": title, "body": body, "meta": meta or {}}
        )
        return True


class _Raises(ActionProvider):
    """A provider that raises instead of returning — the case `provider_failure` exists for."""

    @property
    def name(self) -> str:
        return "boom"

    @property
    def display_name(self) -> str:
        return "Boom"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        raise RuntimeError("the remote index rejected the batch")


class _LongEnvelope(ActionProvider):
    """A provider whose failure carries an explanation longer than any single cap."""

    @property
    def name(self) -> str:
        return "verbose"

    @property
    def display_name(self) -> str:
        return "Verbose"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        return ActionResult(False, error=LONG.what, agent_error=LONG)


class _Fine(ActionProvider):
    @property
    def name(self) -> str:
        return "fine"

    @property
    def display_name(self) -> str:
        return "Fine"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        return ActionResult(True, stdout="done")


def _fire(tmp_path, monkeypatch, provider, config=None, delivery="none") -> _State:
    """One real store trigger through the real dispatch, journal and delivery path."""
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=TRIGGER_ID,
            name="nightly refresh",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            delivery=delivery,
            failure_delivery="inbox",
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": config or {}}},
        )
    )
    state = _State()
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: provider
        orch = object.__new__(RuntimeCoordinator)
        orch.dashboard_state = state
        asyncio.run(
            orch._fire_store_trigger(
                store.get(TRIGGER_ID).trigger, {"trigger_id": TRIGGER_ID}
            )
        )
    finally:
        AP.get_action_provider = real
    return state


def _row(tmp_path) -> dict:
    rows, _ = asyncio.run(ExecutionJournal(tmp_path).list_for_job(TRIGGER_ID, 0, 5))
    assert rows, "the fire wrote no run-history row"
    return rows[0]


def _lines(text: str) -> dict[str, str]:
    """The rendered envelope parsed back into its labelled lines."""
    parsed = {}
    for line in (text or "").splitlines():
        label, _, value = line.partition(": ")
        parsed[label] = value
    return parsed


class TestAProviderThatRaises:
    """`provider_failure()` wraps the raise, and both carriers keep what it built."""

    @pytest.fixture(autouse=True)
    def fired(self, tmp_path, monkeypatch):
        self.home = tmp_path
        self.state = _fire(tmp_path, monkeypatch, _Raises())

    def test_the_run_history_row_carries_the_whole_envelope(self):
        """🔴 THE DEFECT, pinned: this row held `RuntimeError: …` and nothing else."""
        envelope = _row(self.home)["agent_error"]
        assert envelope["code"] == "ERR_ACTION_PROVIDER_FAILED"
        assert "the remote index rejected the batch" in envelope["what"]
        assert envelope["why"], "the row lost WHY"
        assert envelope["fix"], "the row lost FIX"

    def test_the_stored_error_text_renders_all_three_labels(self):
        """A reader of the raw journal sees the same three lines, not a bare exception."""
        assert set(_lines(_row(self.home)["error"])) >= {"WHAT", "WHY", "FIX"}

    def test_the_projection_carries_the_envelope_into_the_feed(self):
        """`ScheduleProjection` is what `/api/triggers/history` answers with."""
        record = schedule_run_to_record(_row(self.home), trigger_id=TRIGGER_ID)
        assert record.agent_error["fix"] == _row(self.home)["agent_error"]["fix"]
        assert set(_lines(record.reason)) >= {"WHAT", "WHY", "FIX"}

    def test_the_notification_carries_the_envelope_too(self):
        """req.43 ac_2: the operator reads the failure here or in history, never live."""
        note = self.state.sent[0]
        assert note["meta"]["event"] == "automation.run.failed"
        assert set(_lines(note["body"])) >= {"WHAT", "WHY", "FIX"}
        envelope = note["meta"]["agent_error"]
        assert envelope["code"] == "ERR_ACTION_PROVIDER_FAILED"
        assert envelope["fix"]

    def test_the_history_and_the_notification_agree(self):
        """Two carriers of one failure that disagreed would cost an operator the diagnosis."""
        assert (
            self.state.sent[0]["meta"]["agent_error"] == _row(self.home)["agent_error"]
        )


class TestARealProviderThatRefuses:
    """The other half of the contract: a provider that RETURNS its envelope, not raises.

    Driven through the real `net-fetch` provider with no URL — its own config refusal, built by
    the provider rather than by the dispatch seam, so this proves the seam forwards an envelope
    it did not construct.
    """

    @pytest.fixture(autouse=True)
    def fired(self, tmp_path, monkeypatch):
        self.home = tmp_path
        self.state = _fire(tmp_path, monkeypatch, NetFetchActionProvider(), config={})

    def test_the_providers_own_envelope_reaches_run_history(self):
        envelope = _row(self.home)["agent_error"]
        assert envelope["code"] == "ERR_NET_FETCH_CONFIG"
        assert envelope["fix"].startswith("set config to")

    def test_the_providers_own_envelope_reaches_the_notification(self):
        """🔴 A returned failure notified with an EMPTY body before this."""
        note = self.state.sent[0]
        assert note["meta"]["agent_error"]["code"] == "ERR_NET_FETCH_CONFIG"
        assert _lines(note["body"])["FIX"].startswith("set config to")


class TestTheBoundIsPerField:
    @pytest.fixture(autouse=True)
    def fired(self, tmp_path, monkeypatch):
        self.home = tmp_path
        self.state = _fire(tmp_path, monkeypatch, _LongEnvelope())

    @pytest.mark.parametrize("carrier", ("history", "notification"))
    def test_a_long_explanation_keeps_all_three_lines(self, carrier):
        """🔴 The regression this file exists for. `[:200]` over the RENDERING keeps
        `WHAT: wwww…` and drops WHY and FIX entirely — the two lines that say what to do.
        """
        envelope = (
            _row(self.home)["agent_error"]
            if carrier == "history"
            else self.state.sent[0]["meta"]["agent_error"]
        )
        assert envelope["what"].startswith("w")
        assert envelope["why"].startswith("y"), "WHY was cut away, not bounded"
        assert envelope["fix"].startswith("f"), "FIX was cut away, not bounded"

    @pytest.mark.parametrize("line", ("what", "why", "fix"))
    def test_each_line_is_bounded_and_says_it_was_trimmed(self, line):
        envelope = _row(self.home)["agent_error"]
        assert len(envelope[line]) == ENVELOPE_FIELD_CAP
        assert envelope[line].endswith("…")

    def test_the_rendered_body_is_longer_than_the_old_whole_envelope_cut(self):
        """The vacuity floor: three bounded lines cannot fit in the 200 characters the
        previous cut allowed, so a body still under it would mean nothing changed."""
        assert len(self.state.sent[0]["body"]) > 200


class TestASuccessCarriesNoGuidance:
    def test_a_successful_fire_records_no_envelope(self, tmp_path, monkeypatch):
        """An empty envelope on a success would light up a failure surface for nothing."""
        state = _fire(tmp_path, monkeypatch, _Fine(), delivery="inbox")
        assert _row(tmp_path)["agent_error"] == {}
        assert _row(tmp_path)["error"] == ""
        assert state.sent[0]["meta"]["event"] == "automation.run.succeeded"
        assert state.sent[0]["meta"].get("agent_error") is None

    def test_the_envelope_survives_the_journals_json_round_trip(
        self, tmp_path, monkeypatch
    ):
        """The row is read back from disk, so an envelope that did not serialize is lost."""
        _fire(tmp_path, monkeypatch, _Raises())
        path = tmp_path / "cron-history" / f"{TRIGGER_ID}.jsonl"
        stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert set(stored["agent_error"]) >= {"code", "what", "why", "fix"}

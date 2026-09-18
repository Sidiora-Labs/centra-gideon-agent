"""``gideon status`` renders the counters ``GET /api/status`` really ships (req.19).

🔴 The defect this file pins: the CLI read ``messages``, ``tool_calls`` and ``crons``
through ``.get(key, 0)``. No version of ``handlers_system.api_status`` has ever produced
those three keys, so every install printed a confident "0 messages, 0 tool calls, 0 cron
jobs" — three numbers that were not measurements of anything.

So no payload here is hand-written. Every one is produced by calling the REAL
``api_status`` handler against a real ``ConsoleState`` over a real aiohttp server, and
the CLI is driven against that server. A test that invented the response body would be
asserting the same fiction the defect was made of.

The schedule and turn totals are seeded through their real owners — ``TriggerStore`` and
``Stats`` — so the rendered numbers are measurements. ``sessions``/``subagents`` are the
counting collaborators ``test_dashboard_status_snapshot`` already stands up that way;
building a live ConversationDirectory needs a provider factory and a real subprocess.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gideon.interfaces.cli.server import _status
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One isolated home behind every seam the status payload reads."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(h))
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: h)
    monkeypatch.setattr(
        "gideon.interfaces.dashboard.state.config_dir", lambda: h, raising=False
    )
    monkeypatch.setattr(
        "gideon.interfaces.dashboard.handlers.triggers.config_dir",
        lambda: h,
        raising=False,
    )
    from gideon.interfaces.dashboard.handlers import updates as _updates_mod

    monkeypatch.setattr(_updates_mod, "_last_update_check", time.time())
    return h


@pytest.fixture
def turns():
    """Add turns to the process-global ``Stats`` and take them back afterwards."""
    from gideon.operations.stats import Stats

    added = []

    def _add(n: int) -> None:
        Stats().inc_turns(n)
        added.append(n)

    yield _add
    for n in added:
        Stats().inc_turns(-n)


def _seed_schedule(home, trigger_id: str, *, enabled: bool = True) -> None:
    from gideon.automation.triggers.models import Trigger
    from gideon.automation.triggers.store import TriggerStore

    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=trigger_id,
            name=trigger_id,
            kind="clock",
            enabled=enabled,
            spec={"kind": "interval", "every_secs": 3600},
        )
    )


def _state(*, sessions: int = 0, subagents: int = 0, lessons: int = 0) -> ConsoleState:
    archive = MagicMock()
    archive.get_lessons.return_value = [
        {"key": f"lesson.{i}", "value_json": '"r"'} for i in range(lessons)
    ]
    memory = MagicMock()
    memory.vector_store = archive
    builder = MagicMock()
    builder.memory = memory
    state = ConsoleState(
        sessions=MagicMock(count=sessions),
        start_time=time.time() - 3600,
        subagents=MagicMock(count=subagents) if subagents else None,
        context_builder=builder,
    )
    state._owner_hash = "test-hash"
    return state


async def _serve(state: ConsoleState, *, drop: tuple[str, ...] = ()) -> TestServer:
    """A gateway serving the REAL ``api_status`` body, minus any keys in ``drop``."""
    from gideon.interfaces.dashboard import handlers_system

    async def _handler(request: web.Request) -> web.Response:
        resp = await handlers_system.api_status(request)
        body = json.loads(resp.body.decode())
        for key in drop:
            body.pop(key, None)
        return web.json_response(body)

    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/status", _handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def _render(state: ConsoleState, capsys, *, drop: tuple[str, ...] = ()) -> str:
    server = await _serve(state, drop=drop)
    try:
        from argparse import Namespace

        await asyncio.to_thread(_status, Namespace(port=server.port))
    finally:
        await server.close()
    return capsys.readouterr().out


def _line(out: str, label: str) -> str:
    for raw in out.splitlines():
        stripped = raw.strip()
        if stripped.startswith(label):
            return stripped[len(label) :].strip()
    raise AssertionError(f"no {label!r} line in:\n{out}")


class TestCountersComeFromTheRealPayload:
    """ac_1 — read the counters the status service actually produces."""

    async def test_the_phantom_counters_are_gone(self, home, capsys):
        """``messages``/``tool_calls``/``crons`` are absent from the real body.

        Asserted on BOTH sides: the payload really does not carry them, and the CLI no
        longer prints a line that could only ever have been a fabricated zero.
        """
        import urllib.request

        def _fetch(port: int) -> dict:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=5
            ) as resp:
                return json.loads(resp.read())

        server = await _serve(_state())
        try:
            body = await asyncio.to_thread(_fetch, server.port)
        finally:
            await server.close()

        for absent in ("messages", "tool_calls", "crons"):
            assert absent not in body, f"{absent} is in the payload after all"

        out = await _render(_state(), capsys)
        assert "Messages" not in out
        assert "Tool calls" not in out
        assert "Cron jobs" not in out

    async def test_sessions_subagents_and_lessons_match_the_payload(self, home, capsys):
        out = await _render(_state(sessions=3, subagents=2, lessons=4), capsys)
        assert _line(out, "Sessions:") == "3"
        assert _line(out, "Subagents:") == "2"
        assert _line(out, "Lessons:") == "4"

    async def test_uptime_is_the_formatted_string_the_payload_carries(
        self, home, capsys
    ):
        state = _state()
        out = await _render(state, capsys)
        assert _line(out, "Uptime:") == state.status_snapshot()["uptime"]


class TestScheduleAndTurnTotals:
    """ac_2 — actual schedule and turn totals, from their real owners."""

    async def test_schedule_totals_come_from_the_trigger_store(self, home, capsys):
        _seed_schedule(home, "clock:a")
        _seed_schedule(home, "clock:b")
        _seed_schedule(home, "clock:c", enabled=False)

        out = await _render(_state(), capsys)

        assert _line(out, "Schedules:") == "3 (2 enabled)"

    async def test_an_empty_schedule_store_reports_a_measured_zero(self, home, capsys):
        """VACUITY: "0" must still be reachable, or "unknown" would just be the new 0."""
        out = await _render(_state(), capsys)
        assert _line(out, "Schedules:") == "0 (0 enabled)"

    async def test_turn_totals_come_from_the_process_counters(
        self, home, capsys, turns
    ):
        from gideon.operations.stats import Stats

        turns(7)
        expected = Stats().snapshot()["total_turns"]

        out = await _render(_state(), capsys)

        assert _line(out, "Turns:") == str(expected)
        assert expected >= 7


class TestAbsentValuesRenderUnknown:
    """ac_2 — display unknown rather than fabricating zero."""

    @pytest.mark.parametrize(
        "key,label",
        [
            ("sessions", "Sessions:"),
            ("subagents", "Subagents:"),
            ("lessons", "Lessons:"),
            ("uptime", "Uptime:"),
            ("cron", "Schedules:"),
            ("stats", "Turns:"),
        ],
    )
    async def test_a_payload_missing_a_key_renders_unknown(
        self, home, capsys, key, label
    ):
        out = await _render(
            _state(sessions=3, subagents=2, lessons=4), capsys, drop=(key,)
        )
        assert _line(out, label) == "unknown"

    async def test_a_present_zero_is_not_reported_as_unknown(self, home, capsys):
        """The whole point of the distinction: a measured 0 must still read as 0."""
        out = await _render(_state(sessions=0, lessons=0), capsys)
        assert _line(out, "Sessions:") == "0"
        assert _line(out, "Subagents:") == "0"
        assert _line(out, "Lessons:") == "0"

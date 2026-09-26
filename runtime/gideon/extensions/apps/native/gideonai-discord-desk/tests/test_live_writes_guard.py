"""``DiscordDeskTransport.send()`` under the DISABLE_LIVE_WRITES kill switch (§1.4).

Every test here drives a FAKE Discord API. Nothing in this module can reach
discord.com, so no test can post a message to a real channel.

The shape being pinned is a TYPED refusal, and each of its three properties is
asserted separately because dropping any one of them re-introduces a distinct bug:

* not typed → a caller cannot tell a suppressed write from a failed one;
* not falsy → every ``if await transport.send(...)`` call site above this one flips
  meaning and starts reporting a refusal as a success;
* not returned (i.e. raised) → core's channel conformance kit fails, because
  ``send()`` may not raise for a well-formed message.

And the whole file rests on a VACUITY FLOOR (``test_guard_off_actually_transmits``):
with the guard off, the same call must really reach the fake API. Without it,
"refused" would pass just as happily against a transport that never sends anything.
"""

from __future__ import annotations

import pytest

from gideon.sdk.channel import OutboundMessage

from discord_desk.transport import DiscordDeskTransport
from discord_desk.writes import (
    ENV_DISABLE_LIVE_WRITES,
    SendRefused,
    guard_flag,
    live_writes_disabled,
)

from test_result_delivery import FakeAPI

_MSG = OutboundMessage(channel_id="500", text="ping")


def _wired() -> tuple[DiscordDeskTransport, FakeAPI]:
    """A CONFIGURED transport over the recording fake API.

    Configured matters: the token gate short-circuits before the guard, so an
    unconfigured transport would return a plain ``False`` and the refusal assertions
    would pass for the wrong reason.
    """
    api = FakeAPI()
    transport = DiscordDeskTransport({"bot_token": "TEST"})
    transport._api = api
    return transport, api


# ── the vacuity floor ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_off_actually_transmits():
    """Guard OFF ⇒ the call really reaches the transport. This is what stops every
    other test in this file from passing vacuously against a dead send path."""
    transport, api = _wired()
    result = await transport.send(_MSG)
    assert result is True
    assert len(api.sent) == 1
    assert api.sent[0]["channel_id"] == "500"
    assert "ping" in api.sent[0]["content"]


# ── the refusal ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_on_refuses_and_transmits_nothing(monkeypatch):
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, api = _wired()

    result = await transport.send(_MSG)

    assert isinstance(result, SendRefused)
    assert api.sent == [], "the guard must suppress the write, not just label it"


@pytest.mark.asyncio
async def test_the_refusal_is_falsy(monkeypatch):
    """Every existing ``if await transport.send(msg):`` call site — core's
    ``ChannelManager`` and everything above it — must keep reading "not delivered"."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired()

    result = await transport.send(_MSG)

    assert bool(result) is False
    assert not result


@pytest.mark.asyncio
async def test_the_refusal_is_returned_not_raised(monkeypatch):
    """``send()`` is contractually forbidden from raising for a well-formed message
    (core's channel conformance kit asserts exactly this), so the refusal rides the
    return value."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired()

    result = await transport.send(_MSG)  # must not raise

    assert isinstance(result, SendRefused)


@pytest.mark.asyncio
async def test_the_refusal_is_attributable(monkeypatch):
    """A refusal a human reads in a log has to say which channel, which target, and
    which switch — otherwise an operator cannot tell why nothing was delivered."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired()

    result = await transport.send(_MSG)

    assert result.channel == "discord"
    assert result.target == "500"
    assert ENV_DISABLE_LIVE_WRITES in result.reason
    assert "discord" in str(result) and "500" in str(result)


@pytest.mark.asyncio
async def test_a_refusal_is_distinguishable_from_a_failure(monkeypatch):
    """The whole point of the type. A send that was ATTEMPTED and failed returns a
    plain ``False``; a send the platform suppressed returns ``SendRefused``. Both are
    falsy, and conflating them is what a bare ``False`` would do."""
    class Boom:
        async def create_message(self, *a, **k):
            raise RuntimeError("boom")

        async def close(self):
            return None

    failing = DiscordDeskTransport({"bot_token": "TEST"})
    failing._api = Boom()
    failure = await failing.send(_MSG)
    assert failure is False
    assert not isinstance(failure, SendRefused)

    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired()
    assert isinstance(await transport.send(_MSG), SendRefused)


@pytest.mark.asyncio
async def test_an_unconfigured_transport_reports_plain_false_not_a_refusal(monkeypatch):
    """The token gate stays AHEAD of the guard: a transport with no token could not
    have written anything, so claiming the guard suppressed a write would be a lie."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    result = await DiscordDeskTransport({}).send(_MSG)
    assert result is False


# ── the flag parse must not drift from core's ────────────────────────────────


def test_absent_var_allows_writes(monkeypatch):
    """The switch is opt-IN. An absent var means writes are ALLOWED — this is not a
    guard-class default-on flag, and getting it backwards would silence every channel
    on a normal gateway."""
    monkeypatch.delenv(ENV_DISABLE_LIVE_WRITES, raising=False)
    assert live_writes_disabled() is False


@pytest.mark.parametrize(
    "raw",
    ["0", "false", "FALSE", "no", "off", "disable", "disabled", "n", "f", " Off "],
)
def test_explicit_falsy_values_turn_the_guard_off(monkeypatch, raw):
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, raw)
    assert live_writes_disabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "", "ture", "maybe", "2"])
def test_any_other_present_value_turns_the_guard_on(monkeypatch, raw):
    """The fail-safe half: a typo (``"ture"``), an unknown token, or an empty string
    keeps the guard ON. A mistyped guard flag must never silently disable the guard."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, raw)
    assert live_writes_disabled() is True


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, True), (True, True), (False, False), (0, False), (1, True), (object(), True)],
)
def test_guard_flag_matches_cores_parse_for_non_string_shapes(value, enabled):
    assert guard_flag(value) is enabled


def test_this_apps_parse_agrees_with_cores_symbol_for_symbol(monkeypatch):
    """The mirror is only safe while it MATCHES. Core's ``guardrails.writes`` is not an
    SDK export, so the runtime code cannot import it — but this test can (test files are
    exempt from the import-boundary lint), which is the whole point: if core ever
    changes the parse, this reds instead of the two halves quietly disagreeing about
    whether writes are on. It is also what keeps the four channel apps' copies in
    agreement with EACH OTHER: each one is pinned to core, so none can drift alone."""
    from gideon.guardrails.flags import guard_flag as core_guard_flag
    from gideon.guardrails.writes import live_writes_disabled as core_disabled

    for raw in ["0", "false", "off", "n", "1", "true", "", "ture", " OFF ", "disabled"]:
        assert guard_flag(raw) == core_guard_flag(raw), raw
        monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, raw)
        assert live_writes_disabled() == core_disabled(), raw

    monkeypatch.delenv(ENV_DISABLE_LIVE_WRITES, raising=False)
    assert live_writes_disabled() == core_disabled() is False

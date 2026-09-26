"""``SlackDeskTransport.send()`` under the DISABLE_LIVE_WRITES kill switch (§1.4).

Every test here drives a MOCK Slack client. Nothing in this module can reach
slack.com, so no test can post a message to a real workspace.

The shape being pinned is a TYPED refusal, and each of its three properties is
asserted separately because dropping any one of them re-introduces a distinct bug:

* not typed → a caller cannot tell a suppressed write from a failed one;
* not falsy → every ``if await transport.send(...)`` call site above this one flips
  meaning and starts reporting a refusal as a success;
* not returned (i.e. raised) → core's channel conformance kit fails, because
  ``send()`` may not raise for a well-formed message.

And the whole file rests on a VACUITY FLOOR (``test_guard_off_actually_transmits``):
with the guard off, the same call must really reach the mock client. Without it,
"refused" would pass just as happily against a transport that never sends anything.
"""

from __future__ import annotations

from typing import Any

import pytest

from gideon.sdk.channel import OutboundMessage

from slack_desk_runtime.transport import SlackDeskTransport
from slack_desk_runtime.writes import (
    ENV_DISABLE_LIVE_WRITES,
    SendRefused,
    guard_flag,
    live_writes_disabled,
)

from slack_desk_helpers import MockSlackDeskClient

_MSG = OutboundMessage(channel_id="C123", text="ping")

#: A configured-but-obviously-fake token. Deliberately NOT in Slack's real xoxb-
#: shape: a token-shaped literal in the tree trips secret scanners, and the transport
#: only ever checks this value for truthiness.
_FAKE_TOKEN = "slack-bot-token-placeholder"


class _StubRuntime:
    """The one attribute ``send()`` reads off the runtime — its Slack client.

    A stub rather than a real :class:`SlackDeskRuntime` on purpose: building the runtime
    connects a Socket-Mode receiver, and this file must not open a socket to assert
    what happens when a write is suppressed.
    """

    def __init__(self, client: Any) -> None:
        self.slack_desk = client


def _wired() -> tuple[SlackDeskTransport, MockSlackDeskClient]:
    """A CONFIGURED transport over the recording mock client.

    Configured matters: the token gate short-circuits before the guard, so an
    unconfigured transport would return a plain ``False`` and the refusal assertions
    would pass for the wrong reason.
    """
    client = MockSlackDeskClient()
    transport = SlackDeskTransport({"bot_token": _FAKE_TOKEN})
    transport._runtime = _StubRuntime(client)  # type: ignore[assignment]
    return transport, client


def _posts(client: MockSlackDeskClient) -> list[dict]:
    return [payload for kind, payload in client.actions if kind == "post"]


# ── the vacuity floor ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_off_actually_transmits():
    """Guard OFF ⇒ the call really reaches the transport. This is what stops every
    other test in this file from passing vacuously against a dead send path."""
    transport, client = _wired()
    result = await transport.send(_MSG)
    assert result is True
    posts = _posts(client)
    assert len(posts) == 1
    assert posts[0]["channel"] == "C123"
    assert "ping" in posts[0]["text"]


# ── the refusal ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_on_refuses_and_transmits_nothing(monkeypatch):
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, client = _wired()

    result = await transport.send(_MSG)

    assert isinstance(result, SendRefused)
    assert _posts(client) == [], "the guard must suppress the write, not just label it"


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

    assert result.channel == "slack"
    assert result.target == "C123"
    assert ENV_DISABLE_LIVE_WRITES in result.reason
    assert "slack" in str(result) and "C123" in str(result)


@pytest.mark.asyncio
async def test_the_refusal_names_the_channel_the_transport_reports(monkeypatch):
    """The refusal's ``channel`` is read off ``self.name``, this bundle's single source
    of truth for the channel name — so a rename cannot leave the two disagreeing."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired()

    result = await transport.send(_MSG)

    assert result.channel == transport.name


@pytest.mark.asyncio
async def test_a_refusal_is_distinguishable_from_a_failure(monkeypatch):
    """The whole point of the type. A send that was ATTEMPTED and failed returns a
    plain ``False``; a send the platform suppressed returns ``SendRefused``. Both are
    falsy, and conflating them is what a bare ``False`` would do."""
    class Boom:
        async def post_message(self, *a, **k):
            raise RuntimeError("boom")

    failing = SlackDeskTransport({"bot_token": _FAKE_TOKEN})
    failing._runtime = _StubRuntime(Boom())  # type: ignore[assignment]
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
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    result = await SlackDeskTransport({}).send(_MSG)
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

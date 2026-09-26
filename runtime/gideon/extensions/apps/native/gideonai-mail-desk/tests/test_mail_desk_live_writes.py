"""``MailDeskTransport.send()`` under the DISABLE_LIVE_WRITES kill switch (§1.4).

Every test here drives a FAKE in-memory SMTP sink. Nothing in this module opens a
socket, so no test can deliver mail to a real address.

An SMTP hand-off is the least reversible write in this repo — once the relay accepts
the message there is no recall, no edit and no delete — which is precisely why this
transport has to honor the platform's kill switch.

The shape being locked is a TYPED refusal, and each of its three properties is
checked separately because dropping any one of them re-introduces a distinct bug:

* not typed → a caller cannot tell a suppressed write from a failed one;
* not falsy → every ``if await transport.send(...)`` call site above this one flips
  meaning and starts reporting a refusal as a success;
* not returned (i.e. raised) → core's channel conformance kit fails, because
  ``send()`` may not raise for a well-formed message.

And the whole file rests on a VACUITY FLOOR (``test_guard_off_actually_transmits``):
with the guard off, the same call must really reach the fake relay. Without it,
"refused" would pass just as happily against a transport that never sends anything.
"""

from __future__ import annotations

import pytest

from gideon.sdk.channel import OutboundMessage, ProviderSettings, save_credential

from mail_desk_runtime.delivery import MailDeskDelivery, ThreadStore
from mail_desk_runtime.settings import CRED_IMAP_PASS, reload_settings
from mail_desk_runtime.transport import MailDeskTransport
from mail_desk_runtime.writes import (
    ENV_DISABLE_LIVE_WRITES,
    SendRefused,
    guard_flag,
    live_writes_disabled,
)

from mail_desk_fakes import FakeSmtpServer

_APP = "gideonai-mail-desk"
AGENT = "agent@example.com"
BOB = "bob@example.com"

_MSG = OutboundMessage(channel_id=BOB, text="ping")


def _configure() -> None:
    """A fully configured outbound mailbox in the app + credential stores.

    Outbound-configured matters: the configuration gate short-circuits before the
    guard, so an unconfigured transport would return a plain ``False`` and the refusal
    assertions would pass for the wrong reason.
    """
    ProviderSettings.update(
        _APP,
        {
            "imap_host": "imap.test", "imap_port": 993, "imap_user": AGENT,
            "smtp_host": "smtp.test", "smtp_port": 587, "smtp_user": AGENT,
            "address": AGENT, "folder": "INBOX", "poll_secs": 60,
            "dm_activation": "always",
        },
    )
    # One app password authenticates both protocols (settings.load_credentials falls
    # back to the IMAP secret), so this is what makes the SMTP half configured too.
    save_credential(CRED_IMAP_PASS, "app-password")
    reload_settings()


def _wired(tmp_path, *, sender: object | None = None) -> tuple[MailDeskTransport, FakeSmtpServer]:
    _configure()
    smtp = sender if sender is not None else FakeSmtpServer()
    transport = MailDeskTransport()
    transport._sender_factory = lambda settings, password: smtp
    transport._delivery = MailDeskDelivery(
        smtp, AGENT, owner_id=AGENT,
        threads=ThreadStore(path_provider=lambda: tmp_path / "threads.json"),
    )
    return transport, smtp  # type: ignore[return-value]


# ── the vacuity floor ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_off_actually_transmits(tmp_path):
    """Guard OFF ⇒ the call really reaches the relay. This is what stops every other
    test in this file from passing emptyly against a dead send path."""
    transport, smtp = _wired(tmp_path)
    result = await transport.send(_MSG)
    assert result is True
    assert len(smtp.sent) == 1
    assert smtp.header("To") == BOB


# ── the refusal ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_on_refuses_and_transmits_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, smtp = _wired(tmp_path)

    result = await transport.send(_MSG)

    assert isinstance(result, SendRefused)
    assert smtp.sent == [], "the guard must suppress the write, not just label it"


@pytest.mark.asyncio
async def test_the_refusal_is_falsy(monkeypatch, tmp_path):
    """Every existing ``if await transport.send(msg):`` call site — core's
    ``ChannelManager`` and everything above it — must keep reading "not delivered"."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired(tmp_path)

    result = await transport.send(_MSG)

    assert bool(result) is False
    assert not result


@pytest.mark.asyncio
async def test_the_refusal_is_returned_not_raised(monkeypatch, tmp_path):
    """``send()`` is agreementually forbidden from raising for a well-formed message
    (core's channel conformance kit asserts precisely this), so the refusal rides the
    return value."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired(tmp_path)

    result = await transport.send(_MSG)  # must not raise

    assert isinstance(result, SendRefused)


@pytest.mark.asyncio
async def test_the_refusal_is_attributable(monkeypatch, tmp_path):
    """A refusal a human reads in a log has to say which channel, which target, and
    which switch — otherwise an operator cannot tell why nothing was delivered."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired(tmp_path)

    result = await transport.send(_MSG)

    assert result.channel == "mail-desk"
    assert result.target == BOB
    assert ENV_DISABLE_LIVE_WRITES in result.reason
    assert "mail-desk" in str(result) and BOB in str(result)


@pytest.mark.asyncio
async def test_a_refusal_is_distinguishable_from_a_failure(monkeypatch, tmp_path):
    """The whole point of the type. A send the relay REFUSED returns a plain ``False``;
    a send the platform suppressed returns ``SendRefused``. Both are falsy, and
    conflating them is what a bare ``False`` would do — a relay failure is worth
    retrying and alerting on, an operator's kill switch is not."""
    failing, _ = _wired(tmp_path, sender=FakeSmtpServer(fail=True))
    failure = await failing.send(_MSG)
    assert failure is False
    assert not isinstance(failure, SendRefused)

    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    transport, _ = _wired(tmp_path)
    assert isinstance(await transport.send(_MSG), SendRefused)


@pytest.mark.asyncio
async def test_an_unconfigured_transport_reports_plain_false_not_a_refusal(monkeypatch):
    """The configuration gate stays AHEAD of the guard: a transport with no SMTP host
    could not have written anything, so claiming the guard suppressed a write would be
    a lie."""
    monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, "1")
    ProviderSettings.update(_APP, {"smtp_host": "", "smtp_user": "", "address": AGENT})
    reload_settings()
    result = await MailDeskTransport().send(_MSG)
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
    exempt from the import-boundary lint), which is the entire reason: if core ever
    changes the parse, this reds instead of the two halves quietly disagreeing about
    whether writes are on. It is also what keeps the four channel apps' copies in
    agreement with EACH OTHER: each one is locked to core, so none can drift alone."""
    from gideon.guardrails.flags import guard_flag as core_guard_flag
    from gideon.guardrails.writes import live_writes_disabled as core_disabled

    for raw in ["0", "false", "off", "n", "1", "true", "", "ture", " OFF ", "disabled"]:
        assert guard_flag(raw) == core_guard_flag(raw), raw
        monkeypatch.setenv(ENV_DISABLE_LIVE_WRITES, raw)
        assert live_writes_disabled() == core_disabled(), raw

    monkeypatch.delenv(ENV_DISABLE_LIVE_WRITES, raising=False)
    assert live_writes_disabled() == core_disabled() is False

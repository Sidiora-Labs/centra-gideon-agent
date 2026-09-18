"""Issue #958 — a dropped inbound channel message says why, at a level someone reads.

Measured on ``origin/main`` before this file existed, with a handler attached to the root
logger at level 1 (below DEBUG): :func:`channel_trust.guard_inbound` emitted **zero log
records** for every one of its denial branches, and it is the function all three shipped
channel apps (Discord, Telegram, email) call directly. A healthy socket that received an
@-mention and correctly discarded it was byte-for-byte indistinguishable from a dead one.
The only line that existed at all was a contextless DEBUG inside
:func:`channel_inbound.deliver_inbound`, which named neither the channel nor the sender —
so it could not answer the one question the operator had ("which channel do I track?") —
and which re-fired on an admission-cache hit.

So the assertions here are about OBSERVABILITY, and the trap they have to avoid is that
"assert something was logged" passes trivially once anything logs at all. Every test
therefore carries a floor in the opposite direction as well: alongside "this drop is
visible" sits "this routine event is NOT", so an implementation that degenerated into
``logger.warning`` on every code path would fail just as loudly as the silent one did.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from gideon.assurance.testing.channel_conformance import CapturingState
from gideon.integrations import channel_inbound as ci
from gideon.integrations import channel_trust as ct
from gideon.integrations.channel_transports.base import ChannelMessage

PROVIDER = "discord"

_OWNED_LOGGERS = (
    "gideon.integrations.channel_trust",
    "gideon.integrations.channel_inbound",
)

SECRET_BODY = "@Bot my api key is sk-live-000111222333 please remember it"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, caplog):
    """Temp entity-settings + SEL home, cleared module caches, and capture at level 1.

    Both module-global caches must be cleared around every test: the admission cache is
    keyed on message identity (not on the store, so ``tmp_path`` alone does not isolate it)
    and the visible-line window is keyed on provider + subject + reason (so one test's
    WARNING would demote the next test's identical drop to DEBUG).

    Capture is set at level 1 rather than DEBUG so that a record emitted BELOW debug — a
    plausible way to make a drop technically "logged" while remaining invisible — would
    still be seen here and fail the level assertions rather than vanish.
    """
    import gideon.core.config.loader as cfg
    import gideon.extensions.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er,
        "_entity_settings_path",
        lambda entity: tmp_path / "entity_settings" / f"{entity}.json",
    )
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    ci.reset_admissions()
    ct.reset_inbound_reports()
    caplog.set_level(1)
    yield tmp_path
    ci.reset_admissions()
    ct.reset_inbound_reports()


def lines(caplog) -> list[tuple[str, str, str]]:
    """Every ``(logger, level, message)`` the inbound path emitted, in order."""
    return [
        (r.name, r.levelname, r.getMessage())
        for r in caplog.records
        if r.name in _OWNED_LOGGERS
    ]


def levels(caplog) -> list[str]:
    return [level for _, level, _ in lines(caplog)]


def _set_policy(axis: str, value: str, provider: str = PROVIDER) -> None:
    store = ct._read_store()
    store[provider] = ct._provider_record(store, provider)
    store[provider]["policies"][axis] = value
    ct._write_store(store)


def _seed_prior_contact(sender_id: str, provider: str = PROVIDER) -> None:
    """Stamp ``sender_id`` as already-notified, so the next denial fires no notification.

    This is the state an unknown sender is in for the 24h after their first message. It is
    the only way to reach the verdict that tells NOBODY anything — no canned reply (that
    needs ``owner_only``) and no owner notification (that needs the window claimed) — which
    is the invisible drop the issue's reproduction did not even reach.
    """
    store = ct._read_store()
    store[provider] = ct._provider_record(store, provider)
    store[provider].setdefault("rate", {})[sender_id] = ct._iso(ct._now())
    ct._write_store(store)


def _msg(text="@Bot hello", sender="user-1", channel="chan-general", mid="m-1"):
    return ChannelMessage(
        channel_id=channel,
        text=text,
        sender=sender,
        thread_id=channel,
        message_id=mid,
        metadata={"sender_name": "Keyur"},
    )


class _Services:
    """The GatewayServices stand-in, delegating exactly as the orchestrator does."""

    def __init__(self, state):
        self.dashboard_state = state

    async def deliver(self, msg, *, is_dm=False):
        async def _turn(state, session, text):
            return None

        return await ci.deliver_inbound(
            cast(Any, self), PROVIDER, msg, is_dm=is_dm, turn_runner=_turn
        )


def test_every_core_path_that_drops_a_message_reports_it(caplog):
    """The headline property. Six ways a message dies before a session; all six speak.

    A fix that lights up one branch while three stay dark is not a fix, so this walks every
    one of them in a single test and asserts a record per path. Five of the six were
    completely silent before this change (the sixth, a missing dashboard state, already
    warned) — so the test fails on unfixed code with a five-entry list of silent paths,
    which is a more useful failure than six separate red tests.
    """
    silent: list[str] = []

    def check(label, drive):
        caplog.clear()
        drive()
        if not lines(caplog):
            silent.append(label)

    check(
        "untracked_channel",
        lambda: ct.guard_inbound(
            None, PROVIDER, "user-1", channel_id="chan-a", is_dm=False, text="@Bot hi"
        ),
    )
    _set_policy("group", "off")
    check(
        "group_policy_off",
        lambda: ct.guard_inbound(
            None, PROVIDER, "user-1", channel_id="chan-b", is_dm=False, text="@Bot hi"
        ),
    )
    check(
        "unknown_sender",
        lambda: ct.guard_inbound(None, PROVIDER, "stranger", is_dm=True, text="hi"),
    )
    _set_policy("dm", "owner_only")
    _seed_prior_contact("quiet-stranger")
    check(
        "unknown_sender/told-nobody",
        lambda: ct.guard_inbound(
            None, PROVIDER, "quiet-stranger", is_dm=True, text="hi"
        ),
    )
    _set_policy("dm", "pairing")
    code = ct.create_pairing_code(PROVIDER)
    pair_msg = _msg(text=code, sender="newbie", mid="m-pair")
    check("paired", lambda: ci.admit(None, PROVIDER, pair_msg, is_dm=True))
    ct.track(PROVIDER, "chan-tracked")
    _set_policy("group", "tracked_only")
    check(
        "no_dashboard_state",
        lambda: asyncio.run(
            _Services(None).deliver(_msg(channel="chan-tracked", mid="m-nostate"))
        ),
    )

    assert (
        silent == []
    ), f"inbound paths that still drop a message with zero logging: {silent}"


def test_an_untracked_channel_mention_warns_and_names_the_channel_to_track(caplog):
    """The issue's reproduction: @-mention in an untracked channel.

    WARNING rather than DEBUG because nobody else was told — the gate deliberately sends no
    in-channel reply (that would let any stranger's room spam the owner) and raises no
    notification, so this line is the message's ONLY trace anywhere in the system.

    The floor is the second half: the very same channel, once tracked, must NOT produce a
    visible line. Without it this test would pass just as well against a blanket
    ``logger.warning`` at the top of the gate, which would bury the real drops in noise.
    """
    verdict = ct.guard_inbound(
        None,
        PROVIDER,
        "user-1",
        channel_id="chan-general",
        is_dm=False,
        text=SECRET_BODY,
    )

    assert verdict.allowed is False and verdict.reason == "untracked_channel"
    assert levels(caplog) == ["WARNING"]
    _, _, msg = lines(caplog)[0]
    assert "untracked_channel" in msg
    assert (
        "chan-general" in msg
    ), "the operator cannot act on a line that omits the channel"
    assert "tracked_only" in msg, "the line must name the policy that refused it"
    assert "track this channel" in msg

    caplog.clear()
    ct.track(PROVIDER, "chan-general")
    allowed = ct.guard_inbound(
        None,
        PROVIDER,
        "user-1",
        channel_id="chan-general",
        is_dm=False,
        text=SECRET_BODY,
    )
    assert allowed.allowed is True
    assert levels(caplog) == [
        "DEBUG"
    ], "an admitted message must not be operator-visible"


def test_group_policy_off_is_reported_as_a_distinct_cause_from_an_untracked_channel(
    caplog,
):
    """``off`` and "not tracked" are one ``track()`` call apart; they must not share a word.

    Before this change both returned ``reason="untracked_channel"``, so the one string the
    operator could see was a lie for half the cases — and the two have completely different
    remedies. The reason now names the :data:`GROUP_POLICIES` value that refused the
    message, which is derived from the existing vocabulary rather than invented.
    """
    ct.track(PROVIDER, "chan-tracked")
    _set_policy("group", "off")

    verdict = ct.guard_inbound(
        None, PROVIDER, "user-1", channel_id="chan-tracked", is_dm=False, text="@Bot hi"
    )

    assert verdict.reason == "group_policy_off"
    assert levels(caplog) == ["WARNING"]
    _, _, msg = lines(caplog)[0]
    assert "group_policy_off" in msg and "policy=off" in msg
    assert "untracked_channel" not in msg

    caplog.clear()
    _set_policy("group", "tracked_only")
    other = ct.guard_inbound(
        None,
        PROVIDER,
        "user-1",
        channel_id="chan-elsewhere",
        is_dm=False,
        text="@Bot hi",
    )
    assert other.reason == "untracked_channel"
    assert "untracked_channel" in lines(caplog)[0][2]


def test_the_level_follows_whether_anybody_was_told(caplog):
    """INFO when the loop was closed with someone; WARNING when it was closed with nobody.

    Both halves are asserted in one test on purpose: separately, each could pass against a
    constant level. Together they can only pass if the level is actually derived from the
    verdict's ``canned_reply`` / ``fired_notification``.
    """
    state = CapturingState()
    told = ct.guard_inbound(
        state, PROVIDER, "stranger", sender_name="Stranger", is_dm=True, text="hi"
    )
    assert told.canned_reply and told.fired_notification is True
    assert (
        state.notifications
    ), "the notification half must still fire — logging replaces nothing"
    assert levels(caplog) == [
        "INFO"
    ], "a denied sender is a decision, not a debug detail"

    caplog.clear()
    ct.reset_inbound_reports()
    _set_policy("dm", "owner_only")
    _seed_prior_contact("quiet-stranger")
    state2 = CapturingState()
    untold = ct.guard_inbound(state2, PROVIDER, "quiet-stranger", is_dm=True, text="hi")
    assert untold.canned_reply == "" and untold.fired_notification is False
    assert state2.notifications == []
    assert levels(caplog) == ["WARNING"], (
        "a drop that reaches neither the sender nor the notification centre is the "
        "invisible case and must be the loud one"
    )


def test_an_admitted_message_is_logged_so_a_live_socket_is_distinguishable_from_a_dead_one(
    caplog,
):
    """The happy path logs too — at DEBUG, because the session is the real evidence.

    This is the other half of the issue's complaint: with nothing logged on success either,
    an operator could not confirm the gateway was receiving anything at all. The floor here
    is the ``>= 1`` count, which is exactly what fails on unfixed code.
    """
    ct.allow_sender(PROVIDER, "friend", "Friend")
    caplog.clear()

    verdict = ct.guard_inbound(None, PROVIDER, "friend", is_dm=True, text="hello")

    assert verdict.allowed is True
    assert levels(caplog) == ["DEBUG"]
    _, _, msg = lines(caplog)[0]
    assert "admitted" in msg and "sender=friend" in msg
    assert "WARNING" not in levels(caplog) and "INFO" not in levels(caplog)


def test_a_repeated_drop_is_demoted_to_debug_and_is_never_silenced(caplog):
    """A bot with MESSAGE_CONTENT sees every message in every visible channel.

    So one WARNING per message in an untracked ``#general`` is itself a flood. The first
    line per subject per window is visible and the rest fall to DEBUG — the same "one alert
    per subject per window" rule the owner notification already used. The floor is that the
    count still equals the number of messages: a suppressed line is demoted, not dropped,
    which is the distinction between flood control and the original bug.
    """
    for i in range(5):
        ct.guard_inbound(
            None,
            PROVIDER,
            f"user-{i}",
            channel_id="chan-busy",
            is_dm=False,
            text="@Bot hi",
        )

    assert levels(caplog) == ["WARNING", "DEBUG", "DEBUG", "DEBUG", "DEBUG"]
    assert len(lines(caplog)) == 5, "every message must leave a trace at SOME level"
    assert "repeat inside the renotify window" in lines(caplog)[1][2]


def test_the_visible_window_is_per_subject_so_a_second_channel_still_announces(caplog):
    """Flood control must not be a global once-per-process latch.

    The subject is what the operator would have to change — the channel here — so tracking
    one channel's noise must not hide a different channel going unheard. The reason is part
    of the key too, so the same channel re-announces when the cause changes.
    """
    for channel in ("chan-a", "chan-b"):
        ct.guard_inbound(
            None, PROVIDER, "user-1", channel_id=channel, is_dm=False, text="@Bot hi"
        )
    assert levels(caplog) == ["WARNING", "WARNING"], "each channel is its own subject"

    caplog.clear()
    ct.guard_inbound(
        None, PROVIDER, "user-1", channel_id="chan-a", is_dm=False, text="@Bot hi"
    )
    assert levels(caplog) == ["DEBUG"]

    caplog.clear()
    _set_policy("group", "off")
    ct.guard_inbound(
        None, PROVIDER, "user-1", channel_id="chan-a", is_dm=False, text="@Bot hi"
    )
    assert levels(caplog) == ["WARNING"]
    assert "group_policy_off" in lines(caplog)[0][2]


def test_an_admission_cache_hit_reports_at_debug_without_re_entering_the_gate(caplog):
    """A provider redelivering a message must not repeat an operator-visible line.

    This is the genuinely routine dedup in the inbound path, and it is the one the old
    ``deliver_inbound`` DEBUG got wrong in the other direction: it re-fired on every
    presentation. The gate is asserted to be entered exactly once, so the second line can
    only be the cache reporting for itself.
    """
    entries: list[str] = []
    real_guard = ct.guard_inbound

    def counting_guard(*a, **kw):
        entries.append(kw.get("channel_id", ""))
        return real_guard(*a, **kw)

    ci_guard = ci.guard_inbound
    assert ci_guard is real_guard

    state = CapturingState()
    msg = _msg(mid="m-dupe")
    services = _Services(state)

    import gideon.integrations.channel_inbound as ci_mod

    ci_mod.guard_inbound = counting_guard  # type: ignore[assignment]
    try:
        asyncio.run(services.deliver(msg))
        first = levels(caplog)
        caplog.clear()
        asyncio.run(services.deliver(msg))
        second = lines(caplog)
    finally:
        ci_mod.guard_inbound = real_guard  # type: ignore[assignment]

    assert len(entries) == 1, "the cache must not re-enter the gate"
    assert first == ["WARNING"]
    assert [level for _, level, _ in second] == ["DEBUG"]
    logger_name, _, message = second[0]
    assert logger_name == "gideon.integrations.channel_inbound"
    assert "already decided" in message


def test_the_pairing_short_circuit_reports_through_the_same_owner(caplog):
    """Redeeming a pairing code still speaks with the gate's single voice.

    ``ci.admit`` hands a message whose whole text is a valid pairing code down to
    ``guard_inbound``, which consumes it and mints ``reason="paired"`` — the one verdict
    that is not a policy denial and so must NOT carry a remedy hint. Asserting the record's
    LOGGER NAME is what pins it to the single owner: a hand-rolled line anywhere on the
    inbound path would satisfy every other assertion here and still be the beginning of a
    second reason vocabulary.
    """
    code = ct.create_pairing_code(PROVIDER)
    caplog.clear()

    verdict = ci.admit(
        None, PROVIDER, _msg(text=code, sender="newbie", mid="m-pair"), is_dm=True
    )

    assert verdict.allowed is False and verdict.reason == "paired"
    assert ct.is_allowed_sender(PROVIDER, "newbie") is True
    assert levels(caplog) == ["INFO"]
    logger_name, _, msg = lines(caplog)[0]
    assert (
        logger_name == "gideon.integrations.channel_trust"
    ), "reporting has exactly one owner"
    assert "reason=paired" in msg
    assert "policy=-" in msg and "pair or allow this sender" not in msg


def test_no_inbound_verdict_is_returned_without_passing_through_the_one_reporter():
    """Every mint site reads ``return report_inbound_verdict(...)`` — checked, not trusted.

    ``guard_inbound`` shipped with five bare ``return TrustVerdict(...)`` statements and
    that is exactly how it ended up silent on all of them: nothing structural said a verdict
    owes anyone an explanation. This is cheap and it catches the sixth branch a future
    policy adds, which is the branch that would otherwise reintroduce this bug.

    Verdict minting lives entirely in the gate now — ``channel_inbound`` delegates even the
    pairing short-circuit it once owned to ``guard_inbound`` — so the full mint-and-report
    rail is asserted on ``channel_trust``; ``channel_inbound`` only has to never mint a bare
    one, which is the invariant that catches a verdict creeping back onto the door.
    """
    from pathlib import Path

    import gideon.integrations.channel_inbound
    import gideon.integrations.channel_trust

    gate_source = Path(str(gideon.integrations.channel_trust.__file__)).read_text(
        encoding="utf-8"
    )
    assert (
        "TrustVerdict(" in gate_source
    ), "channel_trust no longer mints a verdict at all"
    assert (
        "report_inbound_verdict(" in gate_source
    ), "the gate does not reach the reporter"

    for module in (
        gideon.integrations.channel_trust,
        gideon.integrations.channel_inbound,
    ):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        name = module.__name__
        assert "return TrustVerdict(" not in source, (
            f"{name} returns a verdict without reporting it — route it through "
            "channel_trust.report_inbound_verdict so the drop is not silent"
        )


def test_the_log_line_never_carries_the_message_body(caplog):
    """Identifiers yes, content never — across every branch, in one sweep.

    Sender and channel ids are already persisted in the trust store and already carried by
    the ``sender_denied`` SEL row's ``caller_identity``, so logging them adds no exposure
    that did not exist. The body is untrusted third-party content that may hold anything the
    sender typed, and it has no business in an operator's log at any level.
    """
    ct.guard_inbound(None, PROVIDER, "u", channel_id="c", is_dm=False, text=SECRET_BODY)
    ct.guard_inbound(None, PROVIDER, "s", is_dm=True, text=SECRET_BODY)
    ct.allow_sender(PROVIDER, "friend")
    ct.guard_inbound(None, PROVIDER, "friend", is_dm=True, text=SECRET_BODY)

    emitted = lines(caplog)
    assert len(emitted) >= 3, f"expected a line per branch, got {emitted}"
    for _, _, msg in emitted:
        assert "sk-live-000111222333" not in msg
        assert SECRET_BODY not in msg


def test_the_reporter_hands_back_the_verdict_it_was_given(caplog):
    """``return report_inbound_verdict(verdict)`` must be a pure pass-through.

    The single-exit shape only works if reporting cannot alter a trust decision, so this
    pins identity — not equality — and asserts the level rule did not consult anything but
    the verdict. An observability change that could flip ``allowed`` would be far worse than
    the silence it replaced.
    """
    original = ct.TrustVerdict(allowed=False, reason="untracked_channel")
    returned = ct.report_inbound_verdict(
        PROVIDER,
        original,
        sender_id="u",
        channel_id="c",
        is_dm=False,
        policy="tracked_only",
    )

    assert returned is original
    assert returned.allowed is False and returned.reason == "untracked_channel"
    assert levels(caplog) == ["WARNING"]


def test_a_dm_refusal_names_the_sender_and_ITS_remedy(caplog):
    """req 93 ac_3 — the remedy half, on the DM axis.

    The untracked-channel test pins the group remedy ("track this channel"); the DM branch
    has a different one, and it is the branch a stranger's first message takes. Both
    directions are asserted here: the DM line carries the sender-side remedy and NOT the
    channel-side one, so a single hard-coded hint string cannot satisfy both tests.
    """
    verdict = ct.guard_inbound(
        None, PROVIDER, "stranger", sender_name="Stranger", is_dm=True, text=SECRET_BODY
    )

    assert verdict.allowed is False and verdict.reason == "unknown_sender"
    assert len(lines(caplog)) == 1
    _, _, msg = lines(caplog)[0]
    assert "scope=dm" in msg
    assert (
        "sender=stranger" in msg
    ), "the operator cannot act on a line that omits the sender"
    assert (
        "policy=" in msg and "policy=-" not in msg
    ), "the line must name the policy in force"
    assert "pair or allow this sender" in msg
    assert "track this channel" not in msg, "the group remedy leaked onto a DM"
    assert SECRET_BODY not in msg and "sk-live-000111222333" not in msg

"""DiscordDeskTransport against core's channel conformance kit (CE-6 / T7.1).

The kit is the ONE executable statement of the channel contract, shipped by core and
imported through ``gideon.sdk.channel``. It asserts the floor every channel shares —
connect/send echo shapes, capability-dict completeness, health/test shapes, the
unknown-sender flow (canned reply + one actionable owner request, deduped), non-owner
group content entering a session FENCED, and (Discord Desk declares ``edits=True``) a
throttled edit stream that force-flushes on stop.

Discord Desk specifics — the intents bitfield, the 2000-char split, gateway RESUME —
stay in this suite's other test modules.
"""

from __future__ import annotations

import pytest

from gideon.sdk.channel import ChannelContractError, assert_channel_contract

from discord_desk.delivery import _EDIT_MIN_INTERVAL, DiscordDeskDelivery
from discord_desk.transport import DiscordDeskTransport

from test_result_delivery import FakeAPI


def _wired() -> tuple[DiscordDeskTransport, DiscordDeskDelivery, FakeAPI, dict]:
    """A transport + a delivery over the recording fake, with an injectable clock.

    The clock seam is the delivery's own ``_now`` (as in
    ``test_result_delivery``'s throttle test), so the throttle clause is asserted against the
    app's real floor without sleeping.
    """
    api = FakeAPI()
    delivery = DiscordDeskDelivery(api, "42")
    clock = {"t": 0.0}
    delivery._now = lambda: clock["t"]  # type: ignore[method-assign]
    return DiscordDeskTransport({"bot_token": "conformance.token"}), delivery, api, clock


def test_discord_desk_transport_meets_the_channel_contract():
    transport, delivery, api, clock = _wired()
    assert_channel_contract(
        transport,
        delivery=delivery,
        fake_backend=api,
        min_edit_interval=_EDIT_MIN_INTERVAL,
        clock=lambda t: clock.__setitem__("t", t),
        # Discord runs its own gateway WS loop from start_inbound and normalizes each
        # MESSAGE_CREATE in _on_message_create; no generic receive() iterator.
        inbound_via="_on_message_create",
    )


def test_unconfigured_transport_also_conforms():
    """No token ⇒ offline, send() returns False rather than raising."""
    assert_channel_contract(DiscordDeskTransport({}), inbound_via="_on_message_create")


def test_the_kit_is_actually_asserting_something_here():
    """Guard against a vacuous green: break the throttle and the kit must catch it."""

    class Unthrottled(DiscordDeskDelivery):
        async def _maybe_edit(self, st, *, force: bool) -> None:  # type: ignore[no-untyped-def]
            return await super()._maybe_edit(st, force=True)

    api = FakeAPI()
    delivery = Unthrottled(api, "42")
    clock = {"t": 0.0}
    delivery._now = lambda: clock["t"]  # type: ignore[method-assign]
    with pytest.raises(ChannelContractError, match=r"\[streaming\]"):
        assert_channel_contract(
            DiscordDeskTransport({"bot_token": "conformance.token"}),
            delivery=delivery,
            fake_backend=api,
            min_edit_interval=_EDIT_MIN_INTERVAL,
            clock=lambda t: clock.__setitem__("t", t),
            inbound_via="_on_message_create",
        )

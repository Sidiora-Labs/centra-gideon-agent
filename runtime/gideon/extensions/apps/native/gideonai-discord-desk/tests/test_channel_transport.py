"""DiscordDeskTransport — capabilities, ChannelMessage mapping, trust hooks, self-filter.

The wire-protocol traps this module pins are the two Discord introduces beyond the
shared channel floor:

* the bot receives its OWN sends (``MESSAGE_CREATE`` fires for them), so the
  self-authored/other-bot filter is load-bearing — without it the bot answers itself
  forever;
* DM-ness is the ABSENCE of ``guild_id``, not a channel-type guess.

The rest asserts the mapping is honest: capabilities reflect what the delivery
actually implements, and the ChannelMessage carries what core's seam needs.
"""

from __future__ import annotations

import asyncio

import pytest

from gideon.sdk.channel import allow_sender, track
from discord_desk.transport import DiscordDeskTransport, create_provider


class FakeDelivery:
    def __init__(self):
        self.texts: list[tuple[str, str]] = []
        self.guilds: dict[str, str] = {}
        self.interactions: list[dict] = []

    async def deliver_text(self, channel, text, *a, **k):
        self.texts.append((channel, text))
        return "1"

    def note_channel_guild(self, channel_id, guild_id):
        self.guilds[channel_id] = guild_id

    async def resolve_interaction(self, interaction):
        self.interactions.append(interaction)


class FakeSession:
    def __init__(self, key="desk-1"):
        self.key = key
        self.running = False
        self.task = None
        self.appended: list[tuple] = []
        self.queued: list[str] = []

    def append(self, role, text, cls):
        self.appended.append((role, text, cls))

    def queue_append(self, text):
        self.queued.append(text)


class FakeState:
    def __init__(self):
        self.session = FakeSession()
        self.linked: dict = {}
        self._background_tasks: set = set()
        self.notified: list = []

    def get_linked_session(self, thread_key):
        return self.linked.get(thread_key)

    def get_or_create_session(self, app=""):
        self.linked_app = app
        return self.session

    def link_channel(self, key, thread_key, channel_id):
        self.linked[thread_key] = self.session

    def notify(self, *a, **k):
        self.notified.append((a, k))


class FakeServices:
    """The gateway-services handle a transport holds, faked at the SEAM the
    transport actually calls (EA-7): ``deliver_channel_inbound`` delegates to the
    REAL core door with a captured ``turn_runner``, so every trust behavior these
    tests assert — pairing replies, tracked-only drops, fencing, queueing — is the
    real core logic, and only the turn itself is captured instead of run."""

    def __init__(self, state, captured=None):
        self.dashboard_state = state
        self._captured = captured if captured is not None else {}

    async def deliver_channel_inbound(self, provider, msg, *, is_dm=True):
        from gideon.channel_inbound import deliver_inbound

        async def turn_runner(state, session, text):
            self._captured["state"] = state
            self._captured["session"] = session
            self._captured["text"] = text

        return await deliver_inbound(self, provider, msg, is_dm=is_dm, turn_runner=turn_runner)


def _msg(text="hi", channel_id="500", guild_id=None, author_id="42",
         name="Ada", bot=False):
    """A MESSAGE_CREATE payload. No ``guild_id`` key at all == a DM."""
    payload = {
        "id": "9",
        "channel_id": channel_id,
        "content": text,
        "author": {"id": author_id, "username": "ada", "global_name": name, "bot": bot},
    }
    if guild_id is not None:
        payload["guild_id"] = guild_id
    return payload


@pytest.fixture
def transport_with_capture():
    """A transport wired to a fake state + delivery, with the turn captured at the
    door. The admission cache is module-global and `_msg()` reuses one message id,
    so it is reset per test — otherwise one test's verdict answers the next's."""
    from gideon.channel_inbound import reset_admissions

    reset_admissions()
    captured: dict = {}
    t = DiscordDeskTransport({"bot_token": "TEST"})
    state = FakeState()
    t._services = FakeServices(state, captured)
    t._delivery = FakeDelivery()
    return t, state, captured


class TestCapabilities:
    def test_honest_capabilities(self):
        """Every True has an implementation behind it — that's the 'honest' bar."""
        c = DiscordDeskTransport().capabilities()
        assert c.inbound is True
        assert c.threads is True  # a thread IS a channel id in Discord
        assert c.attachments is True  # upload_file (multipart + payload_json)
        assert c.reactions is True  # DiscordDeskDelivery.add_reaction
        assert c.edits is True  # edit_message (also what streaming rides on)
        assert c.rich_text is True  # Discord renders markdown natively
        assert c.typing_indicator is True  # DiscordDeskDelivery.show_typing
        assert c.max_text_len == 2000  # Discord's hard message cap

    def test_declared_capabilities_have_implementations(self):
        """Guards against declaring a capability nothing implements."""
        from discord_desk.delivery import DiscordDeskDelivery

        c = DiscordDeskTransport().capabilities()
        if c.reactions:
            assert callable(DiscordDeskDelivery.add_reaction)
        if c.typing_indicator:
            assert callable(DiscordDeskDelivery.show_typing)
        if c.edits:
            assert callable(DiscordDeskDelivery.start_stream)
        if c.attachments:
            assert callable(DiscordDeskDelivery.upload_attachment)

    def test_name_and_display(self):
        t = DiscordDeskTransport()
        assert t.name == "discord"
        assert t.display_name == "Discord"

    def test_create_provider_returns_transport(self):
        assert type(create_provider({})).__name__ == "DiscordDeskTransport"

    def test_connected_from_token(self):
        assert DiscordDeskTransport({"bot_token": "x"}).connected is True
        assert DiscordDeskTransport({}).connected is False

    @pytest.mark.asyncio
    async def test_health_reflects_token(self):
        assert (await DiscordDeskTransport({"bot_token": "x"}).health())["state"] == "ready"
        assert (await DiscordDeskTransport({}).health())["state"] == "offline"


class TestChannelMessageMapping:
    def test_maps_fields(self):
        t = DiscordDeskTransport()
        cm = t._to_channel_message(_msg(text="yo", channel_id="88", author_id="7"))
        assert cm.channel_id == "88"
        assert cm.text == "yo"
        assert cm.sender == "7"
        assert cm.thread_id == "88"  # a thread is a channel in Discord
        assert cm.message_id == "9"
        assert cm.metadata["sender_name"] == "Ada"
        assert cm.metadata["username"] == "ada"

    def test_guild_id_captured_when_present(self):
        t = DiscordDeskTransport()
        cm = t._to_channel_message(_msg(guild_id="g1"))
        assert cm.metadata["guild_id"] == "g1"

    def test_guild_id_empty_for_a_dm(self):
        t = DiscordDeskTransport()
        cm = t._to_channel_message(_msg())
        assert cm.metadata["guild_id"] == ""

    def test_sender_name_falls_back_to_username(self):
        t = DiscordDeskTransport()
        m = _msg()
        m["author"] = {"id": "3", "username": "nofirst"}
        cm = t._to_channel_message(m)
        assert cm.metadata["sender_name"] == "nofirst"


class TestSelfMessageFilter:
    """MESSAGE_CREATE fires for the bot's OWN sends — the infinite-loop trap."""

    @pytest.mark.asyncio
    async def test_own_message_is_dropped(self, transport_with_capture):
        t, state, captured = transport_with_capture
        t._own_user_id = "bot-1"
        allow_sender("discord", "bot-1")  # even if allowed, it must not route
        await t._on_message_create(_msg(text="my own reply", author_id="bot-1"))
        await asyncio.sleep(0)
        assert "text" not in captured  # never routed — no self-conversation
        assert t._delivery.texts == []

    @pytest.mark.asyncio
    async def test_bot_flag_is_dropped_even_before_ready(self):
        """author.bot covers other bots/webhooks, and works before READY lands."""
        t = DiscordDeskTransport({"bot_token": "TEST"})
        assert t._own_user_id == ""  # READY hasn't arrived
        assert t._is_self_authored(_msg(author_id="other-bot", bot=True)) is True

    def test_own_id_match_is_dropped_without_the_bot_flag(self):
        t = DiscordDeskTransport({"bot_token": "TEST"})
        t._own_user_id = "bot-1"
        assert t._is_self_authored(_msg(author_id="bot-1", bot=False)) is True

    def test_human_message_is_not_self_authored(self):
        t = DiscordDeskTransport({"bot_token": "TEST"})
        t._own_user_id = "bot-1"
        assert t._is_self_authored(_msg(author_id="42")) is False

    @pytest.mark.asyncio
    async def test_ready_captures_own_user_id(self):
        t = DiscordDeskTransport({"bot_token": "TEST"})
        await t._on_ready({"session_id": "s", "user": {"id": "bot-9", "username": "gid"}})
        assert t._own_user_id == "bot-9"


class TestIsDmDerivation:
    """DM-ness is the ABSENCE of guild_id — Discord's actual signal."""

    @pytest.mark.asyncio
    async def test_missing_guild_id_is_treated_as_a_dm(self, transport_with_capture):
        t, state, captured = transport_with_capture
        # An unknown sender in a DM hits the pairing policy (a DM-only outcome);
        # a guild message would instead be dropped silently as untracked.
        await t._on_message_create(_msg(text="hello", author_id="99"))
        await asyncio.sleep(0)
        assert t._delivery.texts, "expected the DM pairing canned reply"
        assert "pairing code" in t._delivery.texts[-1][1]

    @pytest.mark.asyncio
    async def test_present_guild_id_is_not_a_dm(self, transport_with_capture):
        t, state, captured = transport_with_capture
        # Same unknown sender, but in a guild → tracked_only drops it silently.
        await t._on_message_create(_msg(text="hello", guild_id="g1", author_id="99"))
        await asyncio.sleep(0)
        assert t._delivery.texts == []  # no pairing reply — it wasn't a DM
        assert "text" not in captured

    @pytest.mark.asyncio
    async def test_guild_is_noted_for_link_building(self, transport_with_capture):
        t, state, captured = transport_with_capture
        track("discord", "700", "Team Room")
        await t._on_message_create(_msg(text="hi", channel_id="700", guild_id="g1", author_id="55"))
        await asyncio.sleep(0)
        assert t._delivery.guilds == {"700": "g1"}


class TestTrustHooks:
    @pytest.mark.asyncio
    async def test_allowed_dm_routes_to_session(self, transport_with_capture):
        t, state, captured = transport_with_capture
        allow_sender("discord", "42")  # owner/paired sender
        await t._on_message_create(_msg(text="hello", channel_id="dm-42", author_id="42"))
        await asyncio.sleep(0)
        assert captured.get("text") == "hello"
        assert state.linked_app == "discord"

    @pytest.mark.asyncio
    async def test_unknown_dm_sender_gets_canned_reply_not_routed(self, transport_with_capture):
        t, state, captured = transport_with_capture
        # Default DM policy is "pairing"; an unknown sender is denied with the reply.
        await t._on_message_create(_msg(text="hi", author_id="99"))
        await asyncio.sleep(0)
        assert "text" not in captured  # never routed
        assert "pairing code" in t._delivery.texts[-1][1]

    @pytest.mark.asyncio
    async def test_dm_activation_off_short_circuits(self, transport_with_capture):
        from gideon.sdk.channel import ProviderSettings

        from discord_desk.settings import reload_settings

        t, state, captured = transport_with_capture
        allow_sender("discord", "42")
        ProviderSettings.save("gideonai-discord-desk", {"dm_activation": "off"})
        reload_settings()
        await t._on_message_create(_msg(text="hi", author_id="42"))
        await asyncio.sleep(0)
        assert "text" not in captured  # off → nothing routed
        assert t._delivery.texts == []  # and no reply

    @pytest.mark.asyncio
    async def test_dm_activation_off_does_not_gag_guild_channels(self, transport_with_capture):
        """"off" is a DM posture; a tracked guild channel keeps working."""
        from gideon.sdk.channel import ProviderSettings

        from discord_desk.settings import reload_settings

        t, state, captured = transport_with_capture
        ProviderSettings.save("gideonai-discord-desk", {"dm_activation": "off"})
        reload_settings()
        track("discord", "700", "Team Room")
        await t._on_message_create(
            _msg(text="deploy now", channel_id="700", guild_id="g1", author_id="55")
        )
        await asyncio.sleep(0)
        assert "deploy now" in captured.get("text", "")

    @pytest.mark.asyncio
    async def test_tracked_guild_channel_routes_fenced_text(self, transport_with_capture):
        t, state, captured = transport_with_capture
        track("discord", "700", "Team Room")
        await t._on_message_create(
            _msg(text="deploy now", channel_id="700", guild_id="g1", author_id="55")
        )
        await asyncio.sleep(0)
        routed = captured.get("text", "")
        assert "deploy now" in routed
        # non-owner guild content is fenced before it enters the session
        assert "<untrusted_content" in routed

    @pytest.mark.asyncio
    async def test_untracked_guild_channel_is_dropped_silently(self, transport_with_capture):
        t, state, captured = transport_with_capture
        await t._on_message_create(
            _msg(text="spam", channel_id="999", guild_id="g1", author_id="66")
        )
        await asyncio.sleep(0)
        assert "text" not in captured
        assert t._delivery.texts == []  # tracked_only drops silently, no owner spam

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self, transport_with_capture):
        t, state, captured = transport_with_capture
        allow_sender("discord", "42")
        await t._on_message_create(_msg(text="   ", author_id="42"))
        assert "text" not in captured

    @pytest.mark.asyncio
    async def test_canned_reply_failure_does_not_raise_out(self, transport_with_capture):
        t, state, captured = transport_with_capture

        async def boom(*a, **k):
            raise RuntimeError("send failed")

        t._delivery.deliver_text = boom
        await t._on_message_create(_msg(text="hi", author_id="99"))  # no raise

    @pytest.mark.asyncio
    async def test_no_dashboard_state_does_not_crash(self, monkeypatch):
        t = DiscordDeskTransport({"bot_token": "TEST"})
        t._services = FakeServices(None)
        t._delivery = FakeDelivery()
        allow_sender("discord", "42")
        await t._on_message_create(_msg(text="hi", author_id="42"))  # logs, no raise


class TestSessionRouting:
    @pytest.mark.asyncio
    async def test_running_session_queues_instead_of_racing(self, transport_with_capture):
        t, state, captured = transport_with_capture
        allow_sender("discord", "42")
        state.session.running = True
        state.linked["dm-42"] = state.session
        await t._on_message_create(_msg(text="second", channel_id="dm-42", author_id="42"))
        await asyncio.sleep(0)
        assert state.session.queued == ["second"]
        assert "text" not in captured  # not a second concurrent turn

    @pytest.mark.asyncio
    async def test_session_text_is_redacted(self, transport_with_capture):
        t, state, captured = transport_with_capture
        allow_sender("discord", "42")
        await t._on_message_create(
            _msg(text="key sk-ABC123DEF456GHI789JKL012MNO345", channel_id="dm-42", author_id="42")
        )
        await asyncio.sleep(0)
        appended = state.session.appended[-1][1]
        assert "sk-ABC123DEF456GHI789JKL012MNO345" not in appended

    @pytest.mark.asyncio
    async def test_one_session_per_channel(self, transport_with_capture):
        t, state, captured = transport_with_capture
        allow_sender("discord", "42")
        await t._on_message_create(_msg(text="a", channel_id="dm-42", author_id="42"))
        await asyncio.sleep(0)
        assert "dm-42" in state.linked


class TestInteractionRouting:
    @pytest.mark.asyncio
    async def test_interaction_goes_to_the_delivery(self, transport_with_capture):
        t, state, captured = transport_with_capture
        payload = {"id": "i1", "type": 3, "data": {"custom_id": "approve:r1"}}
        await t._on_interaction_create(payload)
        assert t._delivery.interactions == [payload]

    @pytest.mark.asyncio
    async def test_interaction_without_a_delivery_is_a_noop(self):
        t = DiscordDeskTransport({"bot_token": "TEST"})
        await t._on_interaction_create({"id": "i1", "type": 3})  # no raise


class TestOutbound:
    @pytest.mark.asyncio
    async def test_send_without_token_fails(self):
        from gideon.sdk.channel import OutboundMessage

        t = DiscordDeskTransport({})
        assert await t.send(OutboundMessage(channel_id="500", text="hi")) is False

    @pytest.mark.asyncio
    async def test_send_splits_long_text(self):
        from gideon.sdk.channel import OutboundMessage

        sent: list[str] = []

        class API:
            async def create_message(self, channel_id, content, **k):
                sent.append(content)
                return {"id": "1"}

            async def close(self):
                return None

        t = DiscordDeskTransport({"bot_token": "TEST"})
        t._api = API()
        assert await t.send(OutboundMessage(channel_id="500", text="x" * 5000)) is True
        assert len(sent) == 3

    @pytest.mark.asyncio
    async def test_send_failure_is_reported_not_raised(self):
        from gideon.sdk.channel import OutboundMessage

        class API:
            async def create_message(self, *a, **k):
                raise RuntimeError("boom")

            async def close(self):
                return None

        t = DiscordDeskTransport({"bot_token": "TEST"})
        t._api = API()
        assert await t.send(OutboundMessage(channel_id="500", text="hi")) is False


class TestGatewayHelloProbe:
    """test() is the 'gateway hello' probe T4.4 names."""

    @pytest.mark.asyncio
    async def test_without_token_reports_not_configured(self):
        result = await DiscordDeskTransport({}).test()
        assert result["ok"] is False
        assert "No bot token" in result["detail"]

    @pytest.mark.asyncio
    async def test_reports_the_gateway_url_and_session_budget(self, monkeypatch):
        class API:
            def __init__(self, *a, **k):
                pass

            async def get_gateway_bot(self):
                return {"url": "wss://gateway.discord.gg",
                        "session_start_limit": {"remaining": 998}}

            async def close(self):
                return None

        monkeypatch.setattr("discord_desk.transport.DiscordDeskHttpApi", API)
        result = await DiscordDeskTransport({"bot_token": "TEST"}).test()
        assert result["ok"] is True
        assert "wss://gateway.discord.gg" in result["detail"]
        assert "998 session starts remaining" in result["detail"]

    @pytest.mark.asyncio
    async def test_reports_a_bad_token(self, monkeypatch):
        class API:
            def __init__(self, *a, **k):
                pass

            async def get_gateway_bot(self):
                raise RuntimeError("401: Unauthorized")

            async def close(self):
                return None

        monkeypatch.setattr("discord_desk.transport.DiscordDeskHttpApi", API)
        result = await DiscordDeskTransport({"bot_token": "BAD"}).test()
        assert result["ok"] is False
        assert "401" in result["detail"]

    @pytest.mark.asyncio
    async def test_gateway_link_url_discovery_degrades_to_the_default(self):
        from discord_desk.gateway import DEFAULT_GATEWAY_URL

        class API:
            async def get_gateway_bot(self):
                raise RuntimeError("network down")

        t = DiscordDeskTransport({"bot_token": "TEST"})
        t._api = API()
        assert await t._discover_gateway_url() == DEFAULT_GATEWAY_URL

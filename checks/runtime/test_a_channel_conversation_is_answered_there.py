"""A message received during a turn stays queued with its exact channel origin."""

import asyncio

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.integrations import channel_inbound, channel_trust
from gideon.integrations.channel_transports.base import ChannelMessage
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "config.json").write_text('{"providers": []}', encoding="utf-8")
    channel_inbound.reset_admissions()
    yield home
    channel_inbound.reset_admissions()


@pytest.mark.asyncio
async def test_mid_turn_message_is_queued_once_with_provider_and_no_transcript_echo(
    isolated_home,
):
    provider = "local"
    sender = "owner"
    channel_trust.allow_sender(provider, sender)
    config = AppConfig.load()
    state = ConsoleState(
        sessions=ConversationDirectory(config),
        start_time=0,
    )
    gateway = RuntimeCoordinator(config, no_dashboard=True, no_crons=True, no_open=True)
    gateway.dashboard_state = state

    original = ChannelMessage(
        channel_id="direct-message",
        text="first turn message",
        sender=sender,
        thread_id="direct-message",
        message_id="first",
    )
    session = channel_inbound._SessionIngress(
        state, provider, original, original.text
    ).resolve()
    channel_inbound._SessionIngress(state, provider, original, original.text).record(
        session
    )
    before = list(session.messages)

    release = asyncio.Event()
    active_turn = asyncio.create_task(release.wait())
    session.task = active_turn
    queued_message = ChannelMessage(
        channel_id="direct-message",
        text="second turn message",
        sender=sender,
        thread_id="direct-message",
        message_id="second",
    )
    try:
        verdict = await gateway.deliver_channel_inbound(
            provider, queued_message, is_dm=True
        )

        assert verdict.allowed
        assert state.get_linked_session("direct-message") is session
        assert session.messages == before
        assert session.queue_depth == 1
        queued = session.queue_pop()
        assert queued["content"] == (verdict.fenced_text or queued_message.text)
        assert queued["channel"] == provider
        assert session.messages == before
        assert session.task is active_turn
    finally:
        release.set()
        await active_turn

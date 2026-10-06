import json

import pytest

from gideon.core.config.loader import AppConfig
from gideon.cognition.history import ConversationLog
from gideon.engine.session import ConversationDirectory
from gideon.engine.session_map import SessionMap
from gideon.integrations import channel_delivery
from gideon.integrations.channel_inbound import _SessionIngress
from gideon.integrations.channel_transports.base import ChannelMessage, OutboundMessage
from gideon.integrations.channel_transports.reference_echo import ReferenceEchoTransport
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    (tmp_path / 'config.json').write_text('{"providers": []}')
    channel_delivery.register(None)
    yield tmp_path
    channel_delivery.register(None)


def state_at(home):
    return ConsoleState(sessions=ConversationDirectory(AppConfig.load()), start_time=0,
                        conversation_log=ConversationLog(base_dir=home / 'history'))


def persist_chat(state, name, **metadata):
    state.conversation_log.append('dashboard:' + name, 'user', 'Question')
    state.conversation_log.update_metadata('dashboard:' + name, {'lifecycle': 'active', **metadata})


def test_provider_identity_and_link_survive_session_replacement_and_restart(home):
    directory = ConversationDirectory(AppConfig.load())
    key = 'dashboard:chat-1'
    directory.set_channel_link(key, 'thread', 'room', channel_provider='echo-a')
    directory._session_map.set(key, 'sid')
    directory._session_map.forget_session_id(key)
    restored = ConversationDirectory(AppConfig.load())
    assert restored.get_channel_link(key) == ('thread', 'room')
    assert restored.get_channel_provider(key) == 'echo-a'
    assert restored.get_session_for_thread('thread', 'echo-a') == key


@pytest.mark.asyncio
async def test_channel_and_thread_setters_keep_provider(home):
    directory = ConversationDirectory(AppConfig.load())
    directory.set_channel_link('dashboard:chat', 'thread', 'room', channel_provider='echo-a')
    await directory.set_channel('dashboard:chat', 'room-next')
    await directory.set_thread('dashboard:chat', 'thread-next')
    assert directory.get_channel_provider('dashboard:chat') == 'echo-a'
    assert directory.get_channel_link('dashboard:chat') == ('thread-next', 'room-next')


def test_same_thread_ids_on_different_providers_remain_distinct(home):
    sessions = SessionMap()
    sessions.set_channel_link('dashboard:a', 'same', 'room-a', 'echo-a')
    sessions.set_channel_link('dashboard:b', 'same', 'room-b', 'echo-b')
    sessions = SessionMap()
    assert sessions.get_session_for_thread('same', 'echo-a') == 'dashboard:a'
    assert sessions.get_session_for_thread('same', 'echo-b') == 'dashboard:b'
    assert sessions.get_session_for_thread('same') is None


def test_moving_a_chat_evicts_previous_destination_owner_durably(home):
    state = state_at(home)
    state.get_or_create_session('a', app='original')
    state.get_or_create_session('b', app='other-origin')
    state.link_channel('a', 'old', 'room', 'echo-a')
    state.link_channel('b', 'new', 'room-b', 'echo-b')
    state.link_channel('a', 'new', 'room-b', 'echo-b')
    assert state._sessions['a']._app == 'original'
    assert not state._sessions['b']._channel_linked
    restored = SessionMap()
    assert restored.get_session_for_thread('old', 'echo-a') is None
    assert restored.get_session_for_thread('new', 'echo-b') == 'dashboard:a'
    assert restored.get_channel_provider('dashboard:b') == ''


def test_inbound_rehydrates_matching_destination_without_overwriting_initiator(home):
    first = state_at(home)
    actor = {'kind': 'channel_sender', 'provider': 'echo-a', 'subject': 'alice'}
    persist_chat(first, 'chat-1', initiator=actor, app='original')
    first.sessions.set_channel_link('dashboard:chat-1', 'same', 'room', 'echo-a')
    restarted = state_at(home)
    message = ChannelMessage('room', 'next question', sender='alice', thread_id='same')
    session = _SessionIngress(restarted, 'echo-a', message, message.text).resolve()
    assert session.key == 'chat-1'
    assert session._initiator == actor
    assert restarted.channel_provider_for('chat-1') == 'echo-a'
    assert restarted.get_linked_session('same', 'echo-b') is None


@pytest.mark.parametrize('metadata', [{'lifecycle': 'archived'}, {'closed': True}, {'memory_mode': 'temporary'}, {'memory_mode': 'incognito'}])
def test_inbound_does_not_restore_unavailable_or_private_disk_chat(home, metadata):
    state = state_at(home)
    persist_chat(state, 'chat-1', **metadata)
    state.sessions.set_channel_link('dashboard:chat-1', 'thread', 'room', 'echo-a')
    assert state.get_linked_session('thread', 'echo-a') is None


def test_legacy_link_without_known_provider_fails_closed(home):
    state = state_at(home)
    persist_chat(state, 'chat-1', app='some-profile')
    state.sessions.set_channel_link('dashboard:chat-1', 'thread', 'room')
    assert state.channel_provider_for('chat-1') == ''
    assert state.get_linked_session('thread') is None


def test_resident_destination_can_name_and_persist_legacy_provider(home):
    state = state_at(home)
    session = state.get_or_create_session('chat-1')
    state.sessions.set_channel_link('dashboard:chat-1', 'thread', 'room')
    session._channel_provider = 'echo-a'
    session._channel_thread_ts = 'thread'
    session._channel_id = 'room'
    assert state.channel_provider_for('chat-1') == 'echo-a'
    assert SessionMap().get_channel_provider('dashboard:chat-1') == 'echo-a'


@pytest.mark.asyncio
async def test_reference_echo_delivery_selects_persisted_provider_after_restart(home):
    echo_a, echo_b = ReferenceEchoTransport(), ReferenceEchoTransport()
    await echo_a.connect()
    await echo_b.connect()
    channel_delivery.register(echo_a, provider='echo-a')
    channel_delivery.register(echo_b, provider='echo-b')
    state = state_at(home)
    persist_chat(state, 'chat-1')
    state.sessions.set_channel_link('dashboard:chat-1', 'same', 'room', 'echo-b')
    restarted = state_at(home)
    session = restarted.get_linked_session('same', 'echo-b')
    provider = restarted.channel_provider_for(session.key)
    thread, room = restarted.sessions.get_channel_link('dashboard:' + session.key)
    assert await restarted.delivery_for(provider).send(OutboundMessage(room, 'local reply', thread_id=thread))
    assert echo_a.sent == []
    assert echo_b.sent[-1].channel_id == 'room' and echo_b.sent[-1].thread_id == 'same'

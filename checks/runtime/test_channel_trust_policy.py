import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from gideon.core.config.credentials import owner_id_credential, save_credential
from gideon.extensions.providers.entity_routes import _entity_settings_path
from gideon.integrations import channel_trust as trust


@pytest.fixture
def trust_home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    (tmp_path / 'config.json').write_text('{"providers": []}')
    yield tmp_path


@pytest.mark.parametrize('policy', ['owner_only', 'open'])
def test_pairing_redemption_requires_policy_inside_store_transaction(trust_home, policy):
    trust.set_trust_policies('chat', dm=policy, confirm_open=policy == 'open')
    code = trust.create_pairing_code('chat')
    assert not trust.redeem_pairing_code('chat', 'stranger', code)
    assert not trust.is_allowed_sender('chat', 'stranger')
    assert trust.provider_trust('chat')['pairing_active']
    trust.set_trust_policies('chat', dm='pairing')
    assert trust.redeem_pairing_code('chat', 'stranger', code, 'Alice')
    assert trust.provider_trust('chat')['allowed_senders'][0]['name'] == 'Alice'


def test_leaving_pairing_cancels_existing_ticket(trust_home):
    code = trust.create_pairing_code('chat')
    trust.set_trust_policies('chat', dm='owner_only')
    trust.set_trust_policies('chat', dm='pairing')
    assert not trust.redeem_pairing_code('chat', 'stranger', code)
    assert not trust.provider_trust('chat')['pairing_active']


def test_revoke_restarts_stranger_contact_but_deny_unknown_preserves_window(trust_home):
    assert trust.note_unknown_sender(None, 'chat', 'alice')
    trust.allow_sender('chat', 'alice')
    trust.deny_sender('chat', 'alice')
    assert trust.owner_was_asked_about('chat', 'alice')
    first = trust.guard_inbound(None, 'chat', 'alice', text='hello')
    second = trust.guard_inbound(None, 'chat', 'alice', text='again')
    assert not first.allowed and first.canned_reply and first.fired_notification
    assert not second.allowed and not second.canned_reply and not second.fired_notification
    trust.deny_sender('chat', 'alice')
    assert not trust.note_unknown_sender(None, 'chat', 'alice')
    assert trust.provider_trust('chat')['seen_senders'][0]['count'] == 3


def test_refused_messages_count_concurrently_without_content_and_clear_on_allow(trust_home):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: trust.guard_inbound(None, 'chat', 'alice', sender_name='Alice', text='secret body'), range(32)))
    row = trust.provider_trust('chat')['seen_senders'][0]
    assert row['count'] == 32 and row['name'] == 'Alice'
    assert 'secret body' not in _entity_settings_path('channel_trust').read_text()
    trust.allow_sender('chat', 'alice')
    assert trust.provider_trust('chat')['seen_senders'] == []


def test_seen_senders_bounded_and_inbox_held_messages_not_duplicated(trust_home):
    for n in range(30):
        trust.guard_inbound(None, 'chat', str(n), text='message')
    assert len(trust.provider_trust('chat')['seen_senders']) == trust.SEEN_SENDERS_MAX
    trust.guard_inbound(None, 'mail', 'alice', text='secret', hold_for_owner=lambda: True)
    assert trust.provider_trust('mail')['seen_senders'] == []


def test_owner_identity_uses_only_matching_trusted_entry(trust_home):
    save_credential(owner_id_credential('chat'), 'alice')
    trust.allow_sender('chat', 'alice', ' Alice ', via='owner_pairing')
    trust.allow_sender('chat', 'bob', 'Other person')
    assert trust.owner_ref('chat') == {'owner_id': 'alice', 'owner_name': 'Alice', 'owner_source': 'channel'}
    assert [r['sender_id'] for r in trust.provider_trust('chat')['allowed_senders']] == ['bob']
    store = json.loads(_entity_settings_path('channel_trust').read_text())
    store['chat']['allowed_senders']['alice'] = 'malformed'
    _entity_settings_path('channel_trust').write_text(json.dumps(store))
    assert trust.owner_ref('chat')['owner_name'] == ''


def test_shared_owner_identity_is_distinguished_from_channel_binding(trust_home):
    from gideon.core.config.loader import CRED_OWNER_ID
    save_credential(CRED_OWNER_ID, 'legacy')
    assert trust.owner_ref('chat') == {'owner_id': 'legacy', 'owner_name': '', 'owner_source': 'shared'}

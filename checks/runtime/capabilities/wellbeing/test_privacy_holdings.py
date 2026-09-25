import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_privacy import register
from gideon.workspace.capabilities.wellbeing.privacy import PrivacyStore
from gideon.workspace.capabilities.wellbeing.privacy_holdings import OrgHoldingsStore
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError

PASSWORD = 'organization holdings local passphrase'
OLD_VALUE = 'old-private-991@example.test'
NEW_VALUE = 'new-private-992@example.test'


def setup_records(home):
    privacy = PrivacyStore(home)
    person = privacy.create_subject({'request_id': 'subject', 'alias': 'Owner alias', 'relationship': 'self', 'source': 'owner supplied'})
    privacy.consent(person['id'], {'request_id': 'vault', 'revision': 0, 'scope': 'vault', 'granted': True, 'method': 'console confirmation'})
    old = privacy.create_fact(person['id'], {'request_id': 'fact', 'type': 'email', 'label': 'Primary email', 'value': OLD_VALUE, 'passphrase': PASSWORD, 'source': 'owner supplied', 'use_for_scans': False})
    current = privacy.correct_fact(old['id'], {'request_id': 'correct', 'revision': 1, 'value': NEW_VALUE, 'passphrase': PASSWORD})
    return privacy, OrgHoldingsStore(home), person, old, current


def create_org(store, subject, request='org', name='Example Bank', **changes):
    payload = {'request_id': request, 'name': name, 'category': 'finance', 'website': 'https://bank.example/account', 'contact': 'privacy@bank.example', 'source': 'owner supplied'}
    payload.update(changes)
    return store.create_org(subject, payload)


def hold(store, org, fact, request='holding', revision=0, status='held', source='owner supplied'):
    return store.set_holding(org, {'request_id': request, 'revision': revision, 'fact_id': fact['id'], 'fact_revision': fact['revision'], 'status': status, 'source': source})


def declare(store, subject, fact, request='change', start=1, end=2):
    return store.declare_change(subject, {'request_id': request, 'fact_id': fact['id'], 'from_revision': start, 'to_revision': end, 'source': 'owner correction'})


def test_revisioned_organizations_require_vault_consent_and_validate_metadata(tmp_path):
    privacy = PrivacyStore(tmp_path)
    person = privacy.create_subject({'request_id': 'subject', 'alias': 'Owner', 'relationship': 'self', 'source': 'owner'})
    store = OrgHoldingsStore(tmp_path)
    with pytest.raises(MeasurementError) as denied:
        create_org(store, person['id'])
    assert denied.value.status == 403
    privacy.consent(person['id'], {'request_id': 'vault', 'revision': 0, 'scope': 'vault', 'granted': True, 'method': 'owner'})
    row = create_org(store, person['id'])
    assert row['revision'] == 1
    assert row['archived'] is False
    assert row['subject_id'] == person['id']
    assert store.get_org(row['id']) == row
    assert store.list_orgs(person['id']) == [row]
    assert create_org(store, person['id']) == row
    updated = store.update_org(row['id'], {'request_id': 'org-edit', 'revision': 1, 'name': 'Example Credit Union', 'category': 'finance', 'website': '', 'contact': '', 'archived': False})
    assert updated['revision'] == 2
    assert updated['source'] == row['source']
    assert updated['created_at'] == row['created_at']
    assert store.history_org(row['id']) == [row, updated]
    with pytest.raises(MeasurementError) as stale:
        store.update_org(row['id'], {'request_id': 'stale', 'revision': 1, 'name': 'Stale'})
    assert stale.value.status == 409
    for request, changes in [('bad-scheme', {'website': 'file:///private'}), ('credentials', {'website': 'https://user:pass@example.test'}), ('empty-name', {'name': ''}), ('bad-archive', {'archived': 'yes'}), ('extra', {'unexpected': True})]:
        with pytest.raises(MeasurementError):
            store.update_org(row['id'], {'request_id': request, 'revision': 2, **changes})
    assert store.get_org(row['id']) == updated
    assert OLD_VALUE.encode() not in store.path.read_bytes()
    assert NEW_VALUE.encode() not in store.path.read_bytes()
    assert PASSWORD.encode() not in store.path.read_bytes()


def test_holdings_are_subject_scoped_revisioned_and_never_reveal_values(tmp_path):
    privacy, store, person, old, current = setup_records(tmp_path)
    org = create_org(store, person['id'])
    row = hold(store, org['id'], old)
    assert row['revision'] == 1
    assert row['status'] == 'held'
    assert row['fact_revision'] == 1
    assert 'value' not in row
    assert store.list_holdings(org['id']) == [row]
    assert hold(store, org['id'], old) == row
    changed = hold(store, org['id'], current, request='holding-current', revision=1, status='unknown')
    assert changed['revision'] == 2
    assert changed['fact_revision'] == 2
    assert changed['status'] == 'unknown'
    assert store.history_holding(row['id']) == [row, changed]
    with pytest.raises(MeasurementError, match='reload'):
        hold(store, org['id'], current, request='stale-holding', revision=1)
    other = privacy.create_subject({'request_id': 'other-subject', 'alias': 'Other', 'relationship': 'other', 'source': 'owner'})
    privacy.consent(other['id'], {'request_id': 'other-vault', 'revision': 0, 'scope': 'vault', 'granted': True, 'method': 'owner'})
    foreign = privacy.create_fact(other['id'], {'request_id': 'foreign-fact', 'type': 'phone', 'label': 'Phone', 'value': '+15555551212', 'passphrase': PASSWORD, 'source': 'owner', 'use_for_scans': False})
    with pytest.raises(MeasurementError, match='matching subject'):
        hold(store, org['id'], foreign, request='cross-subject', revision=2)
    archived_org = store.update_org(org['id'], {'request_id': 'archive-org', 'revision': 1, 'archived': True})
    assert archived_org['archived']
    with pytest.raises(MeasurementError, match='active organization'):
        hold(store, org['id'], current, request='archived-holding', revision=2)
    text = json.dumps(store.list_holdings(org['id']))
    assert OLD_VALUE not in text
    assert NEW_VALUE not in text
    assert PASSWORD not in text


def test_change_atomically_propagates_to_every_eligible_holding_and_derives_progress(tmp_path):
    privacy, store, person, old, current = setup_records(tmp_path)
    first = create_org(store, person['id'], request='org-one', name='First Bank')
    second = create_org(store, person['id'], request='org-two', name='Second Utility', category='utility', website='', contact='help@utility.example')
    third = create_org(store, person['id'], request='org-three', name='Former Store')
    first_holding = hold(store, first['id'], old, request='holding-one')
    second_holding = hold(store, second['id'], old, request='holding-two', status='unknown')
    third_holding = hold(store, third['id'], old, request='holding-three', status='removed')
    change = declare(store, person['id'], old)
    assert change['fact_id'] == old['id']
    assert change['from_revision'] == 1
    assert change['to_revision'] == current['revision']
    assert change['progress'] == {'pending': 2, 'updated': 0, 'removed': 0, 'total': 2}
    assert [row['org_id'] for row in change['targets']] == sorted([first['id'], second['id']])
    assert all(row['attestation'] == 'user_reported' and row['evidence'] == '' for row in change['targets'])
    assert store.list_holdings(first['id'])[0]['status'] == 'update_pending'
    assert store.list_holdings(second['id'])[0]['status'] == 'update_pending'
    assert store.list_holdings(third['id'])[0] == third_holding
    assert len(store.history_holding(first_holding['id'])) == 2
    assert len(store.history_holding(second_holding['id'])) == 2
    assert store.get_change(change['id']) == change
    assert store.list_changes(person['id']) == [change]
    assert declare(store, person['id'], old) == change
    with pytest.raises(MeasurementError, match='already pending'):
        declare(store, person['id'], old, request='second-change')
    with pytest.raises(MeasurementError, match='pending change'):
        hold(store, first['id'], current, request='edit-pending', revision=2)
    audit = privacy.audit(person['id'])
    event = next(row for row in audit if row['operation'] == 'change_declared')
    assert event['from_revision'] == 1
    assert event['to_revision'] == 2
    assert OLD_VALUE not in json.dumps(audit)
    assert NEW_VALUE not in json.dumps(audit)


def test_user_attested_dispositions_preserve_evidence_and_histories(tmp_path):
    privacy, store, person, old, current = setup_records(tmp_path)
    updated_org = create_org(store, person['id'], request='org-update', name='Updated Bank')
    removed_org = create_org(store, person['id'], request='org-remove', name='Removed Utility')
    update_holding = hold(store, updated_org['id'], old, request='holding-update')
    remove_holding = hold(store, removed_org['id'], old, request='holding-remove')
    change = declare(store, person['id'], old)
    update = store.settle(change['id'], updated_org['id'], {'request_id': 'settle-update', 'revision': 1, 'status': 'updated', 'evidence': 'Owner received confirmation number 812'})
    assert update['progress'] == {'pending': 1, 'updated': 1, 'removed': 0, 'total': 2}
    update_target = next(row for row in update['targets'] if row['org_id'] == updated_org['id'])
    assert update_target['status'] == 'updated'
    assert update_target['revision'] == 2
    assert update_target['attestation'] == 'user_reported'
    assert update_target['evidence'] == 'Owner received confirmation number 812'
    advanced = store.list_holdings(updated_org['id'])[0]
    assert advanced['status'] == 'held'
    assert advanced['fact_revision'] == current['revision']
    assert advanced['change_id'] is None
    assert [row['status'] for row in store.history_holding(update_holding['id'])] == ['held', 'update_pending', 'held']
    with pytest.raises(MeasurementError, match='updated or removed'):
        store.settle(change['id'], removed_org['id'], {'request_id': 'bad-status', 'revision': 1, 'status': 'pending', 'evidence': ''})
    removed = store.settle(change['id'], removed_org['id'], {'request_id': 'settle-remove', 'revision': 1, 'status': 'removed', 'evidence': 'Owner verified account deletion'})
    assert removed['progress'] == {'pending': 0, 'updated': 1, 'removed': 1, 'total': 2}
    removed_holding = store.list_holdings(removed_org['id'])[0]
    assert removed_holding['status'] == 'removed'
    assert removed_holding['fact_revision'] == old['revision']
    assert [row['status'] for row in store.history_holding(remove_holding['id'])] == ['held', 'update_pending', 'removed']
    assert [row['status'] for row in store.history_target(change['id'], updated_org['id'])] == ['pending', 'updated']
    assert [row['status'] for row in store.history_target(change['id'], removed_org['id'])] == ['pending', 'removed']
    assert store.settle(change['id'], updated_org['id'], {'request_id': 'settle-update', 'revision': 1, 'status': 'updated', 'evidence': 'Owner received confirmation number 812'}) == update
    with pytest.raises(MeasurementError, match='no longer pending'):
        store.settle(change['id'], updated_org['id'], {'request_id': 'settle-again', 'revision': 2, 'status': 'removed', 'evidence': ''})
    events = [row for row in privacy.audit(person['id']) if row['operation'] == 'change_disposition']
    assert [row['status'] for row in events] == ['updated', 'removed']
    assert all(row['attestation'] == 'user_reported' for row in events)
    assert 'confirmation number' not in json.dumps(events)


@pytest.mark.parametrize('start,end', [(0, 2), (1, 1), (2, 1), (1, 3), (True, 2), (1, '2')])
def test_change_rejects_invalid_or_noncurrent_revision_ranges(tmp_path, start, end):
    _, store, person, old, _ = setup_records(tmp_path)
    org = create_org(store, person['id'])
    hold(store, org['id'], old)
    with pytest.raises(MeasurementError):
        declare(store, person['id'], old, start=start, end=end)
    assert store.list_changes(person['id']) == []
    assert store.list_holdings(org['id'])[0]['status'] == 'held'


def test_concurrent_change_declaration_has_one_receipt_and_one_propagation(tmp_path):
    _, store, person, old, _ = setup_records(tmp_path)
    org = create_org(store, person['id'])
    holding = hold(store, org['id'], old)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: OrgHoldingsStore(tmp_path).declare_change(person['id'], {'request_id': 'same-change', 'fact_id': old['id'], 'from_revision': 1, 'to_revision': 2, 'source': 'owner'}), range(3)))
    assert rows[0] == rows[1] == rows[2]
    assert len(store.list_changes(person['id'])) == 1
    assert [row['status'] for row in store.history_holding(holding['id'])] == ['held', 'update_pending']


@pytest.mark.asyncio
async def test_native_tools_read_metadata_only_and_cannot_attest_mutate_or_reveal(tmp_path):
    _, store, person, old, _ = setup_records(tmp_path)
    org = create_org(store, person['id'])
    holding = hold(store, org['id'], old)
    change = declare(store, person['id'], old)
    provider = WellbeingProvider(tmp_path)
    operations = (await provider.list_tools())[0].parameters['properties']['operation']['enum']
    allowed = [('privacy_organizations', person['id']), ('privacy_organization', org['id']), ('privacy_organization_history', org['id']), ('privacy_holdings', org['id']), ('privacy_holding_history', holding['id']), ('privacy_changes', person['id']), ('privacy_change', change['id'])]
    for operation, identity in allowed:
        assert operation in operations
        result = await provider.invoke('wellbeing_records', {'operation': operation, 'id': identity})
        assert result.success
        assert OLD_VALUE not in result.output
        assert NEW_VALUE not in result.output
        assert PASSWORD not in result.output
        assert 'cipher' not in result.output
    for operation in ('privacy_organization_create', 'privacy_holding_set', 'privacy_change_declare', 'privacy_change_settle', 'privacy_change_draft'):
        result = await provider.invoke('wellbeing_records', {'operation': operation, 'id': change['id'], 'payload': {'passphrase': PASSWORD, 'value': NEW_VALUE}})
        assert not result.success
        assert OLD_VALUE not in str(result)
        assert NEW_VALUE not in str(result)
        assert PASSWORD not in str(result)


@pytest.mark.asyncio
async def test_real_http_workflow_is_home_scoped_no_store_and_has_no_send_route(tmp_path):
    app, isolated_app = web.Application(), web.Application()
    register(app, tmp_path / 'one')
    register(isolated_app, tmp_path / 'two')
    base = '/api/capabilities/wellbeing/privacy'
    async with TestClient(TestServer(app)) as client, TestClient(TestServer(isolated_app)) as isolated:
        created = await client.post(base + '/subjects', json={'request_id': 'subject', 'alias': 'Owner', 'relationship': 'self', 'source': 'owner'})
        person = await created.json()
        subject = base + '/subjects/' + person['id']
        await client.post(subject + '/consents', json={'request_id': 'vault', 'revision': 0, 'scope': 'vault', 'granted': True, 'method': 'owner'})
        first = await (await client.post(subject + '/facts', json={'request_id': 'fact', 'type': 'email', 'label': 'Email', 'value': OLD_VALUE, 'passphrase': PASSWORD, 'source': 'owner', 'use_for_scans': False})).json()
        current = await (await client.put(base + '/facts/' + first['id'], json={'request_id': 'correct', 'revision': 1, 'value': NEW_VALUE, 'passphrase': PASSWORD})).json()
        response = await client.post(subject + '/organizations', json={'request_id': 'org', 'name': 'Bank', 'category': 'finance', 'website': 'https://bank.example', 'contact': 'privacy@bank.example', 'source': 'owner'})
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        org = await response.json()
        holding = await (await client.post(base + '/organizations/' + org['id'] + '/holdings', json={'request_id': 'holding', 'revision': 0, 'fact_id': first['id'], 'fact_revision': first['revision'], 'status': 'held', 'source': 'owner'})).json()
        change = await (await client.post(subject + '/changes', json={'request_id': 'change', 'fact_id': first['id'], 'from_revision': first['revision'], 'to_revision': current['revision'], 'source': 'owner'})).json()
        assert change['progress']['pending'] == 1
        response = await client.post(base + '/changes/' + change['id'] + '/organizations/' + org['id'], json={'request_id': 'settle', 'revision': 1, 'status': 'updated', 'evidence': 'Owner saw confirmation'})
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        settled = await response.json()
        assert settled['progress'] == {'pending': 0, 'updated': 1, 'removed': 0, 'total': 1}
        assert len((await (await client.get(base + '/organizations/' + org['id'] + '/history')).json())['history']) == 1
        assert len((await (await client.get(base + '/holdings/' + holding['id'] + '/history')).json())['history']) == 3
        assert [row['status'] for row in (await (await client.get(base + '/changes/' + change['id'] + '/organizations/' + org['id'] + '/history')).json())['history']] == ['pending', 'updated']
        assert (await isolated.get(base + '/organizations/' + org['id'])).status == 404
        assert (await isolated.get(base + '/changes/' + change['id'])).status == 404
        assert (await client.post(base + '/changes/' + change['id'] + '/organizations/' + org['id'] + '/send', json={})).status == 404
        serialized = json.dumps(await (await client.get(subject + '/changes')).json())
        assert OLD_VALUE not in serialized
        assert NEW_VALUE not in serialized
        assert PASSWORD not in serialized
        raw = OrgHoldingsStore(tmp_path / 'one').path.read_bytes()
        assert OLD_VALUE.encode() not in raw
        assert NEW_VALUE.encode() not in raw
        assert PASSWORD.encode() not in raw

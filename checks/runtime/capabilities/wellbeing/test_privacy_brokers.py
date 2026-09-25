import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_brokers import register
from gideon.workspace.capabilities.wellbeing.privacy import PrivacyStore
from gideon.workspace.capabilities.wellbeing.privacy_brokers import (
    PrivacyBrokerStore,
    VerifiedBrokerObservation,
)
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def setup_store(home, *, scan=True, submit=True):
    privacy = PrivacyStore(home)
    subject = privacy.create_subject({'request_id': 'subject', 'alias': 'Owner', 'relationship': 'self', 'source': 'owner'})
    for scope, granted in (('broker_scan', scan), ('broker_submit', submit)):
        privacy.consent(subject['id'], {'request_id': scope, 'revision': 0, 'scope': scope, 'granted': granted, 'method': 'owner choice'})
    return privacy, PrivacyBrokerStore(home), subject


def broker(store, request='broker', name='Example Search'):
    return store.create_broker({
        'request_id': request,
        'name': name,
        'website': 'https://broker.example/profile',
        'optout_url': 'https://broker.example/remove',
        'source': 'owner supplied',
    })


def case(store, subject, broker_row, request='case'):
    return store.create_case(subject['id'], {'request_id': request, 'broker_id': broker_row['id']})


def observe(store, row, outcome, evidence, request):
    return store.record_user_observation(row['id'], {
        'request_id': request,
        'revision': row['revision'],
        'outcome': outcome,
        'evidence': evidence,
    })


def move(store, row, state, request, reason='Owner completed this step'):
    return store.transition(row['id'], {
        'request_id': request,
        'revision': row['revision'],
        'state': state,
        'reason': reason,
    })


def verified(outcome='not_found', evidence='scanner://run/42'):
    return VerifiedBrokerObservation(
        outcome=outcome,
        verifier='read-only broker scanner',
        checked_at=datetime.now(timezone.utc).isoformat(),
        evidence_ref=evidence,
    )


def test_broker_catalog_case_and_owner_observation_are_durable_and_subject_scoped(tmp_path):
    privacy, store, person = setup_store(tmp_path)
    catalog = broker(store)
    created = case(store, person, catalog)
    assert created['state'] == 'unscanned'
    assert created['revision'] == 1
    assert created['evidence_basis'] == 'none'
    assert created['allowed_transitions'] == []
    found = observe(store, created, 'found', 'Owner saw a matching name and address', 'found')
    assert found['state'] == 'found'
    assert found['revision'] == 2
    assert found['evidence_basis'] == 'user_attested'
    assert found['evidence'] == 'Owner saw a matching name and address'
    assert found['allowed_transitions'] == ['optout_in_progress', 'human_task_queued']
    reopened = PrivacyBrokerStore(tmp_path)
    listed = reopened.list_cases(person['id'])
    assert len(listed) == 1
    assert listed[0]['broker']['name'] == 'Example Search'
    assert listed[0]['broker']['optout_url'] == 'https://broker.example/remove'
    assert reopened.get_case(created['id']) == found
    assert [row['state'] for row in reopened.history(created['id'])] == ['unscanned', 'found']
    assert [row['operation'] for row in reopened.events(created['id'])] == ['created', 'owner_observation']
    other = privacy.create_subject({'request_id': 'other', 'alias': 'Housemate', 'relationship': 'household', 'source': 'owner'})
    privacy.consent(other['id'], {'request_id': 'other-scan', 'revision': 0, 'scope': 'broker_scan', 'granted': True, 'method': 'owner choice'})
    other_case = case(store, other, catalog, 'other-case')
    assert other_case['id'] != created['id']
    assert [row['id'] for row in store.list_cases(other['id'])] == [other_case['id']]
    assert [row['id'] for row in store.list_cases(person['id'])] == [created['id']]
    audit = privacy.audit(person['id'])
    assert [row['operation'] for row in audit if row['operation'].startswith('broker_case_')] == ['broker_case_created', 'broker_case_owner_observation']
    assert audit[-1]['evidence_basis'] == 'user_attested'
    assert 'matching name' not in json.dumps(audit)


def test_manual_optout_lifecycle_requires_submit_consent_and_never_confirms_removal(tmp_path):
    privacy, store, person = setup_store(tmp_path)
    row = observe(store, case(store, person, broker(store)), 'found', 'Owner found listing', 'seen')
    row = move(store, row, 'optout_in_progress', 'start')
    assert row['state'] == 'optout_in_progress'
    assert row['evidence_basis'] == 'user_attested'
    row = move(store, row, 'submitted', 'submitted', 'Owner reports submitting the broker form')
    assert row['state'] == 'submitted'
    row = move(store, row, 'verification_pending', 'pending', 'Owner reports the broker acknowledged receipt')
    assert row['state'] == 'verification_pending'
    row = move(store, row, 'awaiting_processing', 'processing', 'Owner reports the broker requested processing time')
    assert row['state'] == 'awaiting_processing'
    assert 'confirmed_removed' not in row['allowed_transitions']
    with pytest.raises(MeasurementError, match='Invalid manual'):
        move(store, row, 'confirmed_removed', 'manual-confirm')
    with pytest.raises(MeasurementError, match='Invalid manual'):
        move(store, row, 'reappeared', 'manual-reappear')
    event = store.events(row['id'])[-1]
    assert event['operation'] == 'manual_transition'
    assert event['state'] == 'awaiting_processing'
    assert store.get_case(row['id'])['state'] == 'awaiting_processing'
    denied_home = tmp_path / 'denied'
    _, denied_store, denied_person = setup_store(denied_home, submit=False)
    denied = observe(denied_store, case(denied_store, denied_person, broker(denied_store)), 'found', 'Owner saw listing', 'seen')
    with pytest.raises(MeasurementError, match='broker_submit'):
        move(denied_store, denied, 'optout_in_progress', 'denied')
    assert denied_store.get_case(denied['id'])['state'] == 'found'
    privacy.consent(person['id'], {'request_id': 'revoke-submit', 'revision': 1, 'scope': 'broker_submit', 'granted': False, 'method': 'owner revoked'})
    assert store.get_case(row['id'])['state'] == 'awaiting_processing'


def test_verified_scanner_boundary_is_the_only_confirmed_removed_path(tmp_path):
    _, store, person = setup_store(tmp_path)
    row = observe(store, case(store, person, broker(store)), 'found', 'Owner saw listing', 'seen')
    row = move(store, row, 'optout_in_progress', 'start')
    row = move(store, row, 'submitted', 'submit')
    row = move(store, row, 'verification_pending', 'verify')
    with pytest.raises(MeasurementError, match='scanner adapter'):
        store.apply_verified_observation(row['id'], {'outcome': 'not_found'})
    with pytest.raises(MeasurementError, match='must be found or not_found'):
        store.apply_verified_observation(row['id'], verified('blocked'))
    with pytest.raises(MeasurementError, match='offset timestamp'):
        store.apply_verified_observation(row['id'], VerifiedBrokerObservation('not_found', 'scanner', 'yesterday', 'receipt'))
    removed = store.apply_verified_observation(row['id'], verified())
    assert removed['state'] == 'confirmed_removed'
    assert removed['evidence_basis'] == 'verified_rescan'
    assert removed['verifier'] == 'read-only broker scanner'
    assert removed['evidence'] == 'scanner://run/42'
    assert removed['allowed_transitions'] == []
    with pytest.raises(MeasurementError, match='does not match'):
        store.apply_verified_observation(removed['id'], verified('not_found', 'scanner://run/43'))
    reappeared = store.apply_verified_observation(removed['id'], verified('found', 'scanner://run/44'))
    assert reappeared['state'] == 'reappeared'
    assert reappeared['evidence_basis'] == 'verified_rescan'
    assert reappeared['allowed_transitions'] == ['optout_in_progress', 'human_task_queued']
    history = store.history(row['id'])
    assert history[-2]['state'] == 'confirmed_removed'
    assert history[-1]['state'] == 'reappeared'
    events = store.events(row['id'])
    assert events[-2]['operation'] == 'verified_observation'
    assert events[-2]['outcome'] == 'not_found'
    assert events[-1]['outcome'] == 'found'


@pytest.mark.parametrize('initial,outcome', [
    ('unscanned', 'found'),
    ('unscanned', 'not_found'),
    ('unscanned', 'indirect_exposure'),
    ('unscanned', 'blocked'),
])
def test_each_owner_scan_outcome_is_explicitly_user_attested(tmp_path, initial, outcome):
    _, store, person = setup_store(tmp_path)
    row = case(store, person, broker(store))
    assert row['state'] == initial
    observed = observe(store, row, outcome, 'Owner observation only', 'observe')
    assert observed['state'] == outcome
    assert observed['evidence_basis'] == 'user_attested'
    assert 'verified_at' not in observed
    assert 'verifier' not in observed
    assert observed['next_recheck_at'] > observed['updated_at'] or outcome == 'unscanned'


def test_owner_rescans_settled_observation_but_cannot_interrupt_submission(tmp_path):
    _, store, person = setup_store(tmp_path)
    row = observe(store, case(store, person, broker(store)), 'not_found', 'No result seen', 'none')
    changed = observe(store, row, 'found', 'Listing appeared later', 'later')
    assert changed['state'] == 'found'
    assert changed['evidence'] == 'Listing appeared later'
    changed = observe(store, changed, 'blocked', 'Challenge blocked owner check', 'blocked')
    assert changed['state'] == 'blocked'
    with pytest.raises(MeasurementError, match='Invalid manual'):
        move(store, changed, 'submitted', 'skip-start')
    queued = move(store, changed, 'human_task_queued', 'queue', 'Owner needs browser help')
    assert queued['state'] == 'human_task_queued'
    assert queued['reason'] == 'Owner needs browser help'
    with pytest.raises(MeasurementError, match='must complete'):
        observe(store, queued, 'found', 'Owner retried', 'retry')
    resumed = move(store, queued, 'optout_in_progress', 'resume')
    assert resumed['state'] == 'optout_in_progress'
    with pytest.raises(MeasurementError, match='must complete'):
        observe(store, resumed, 'not_found', 'Owner claims gone', 'premature')


def test_recheck_is_revisioned_idempotent_and_preserves_state(tmp_path):
    _, store, person = setup_store(tmp_path)
    row = observe(store, case(store, person, broker(store)), 'found', 'Owner saw listing', 'seen')
    payload = {'request_id': 'recheck', 'revision': row['revision']}
    due = store.request_recheck(row['id'], payload)
    again = store.request_recheck(row['id'], payload)
    assert again == due
    assert due['state'] == 'found'
    assert due['revision'] == row['revision'] + 1
    assert due['next_recheck_at'] <= due['updated_at']
    assert [event['operation'] for event in store.events(row['id'])][-1] == 'recheck_requested'
    with pytest.raises(MeasurementError, match='changed; reload'):
        store.request_recheck(row['id'], {'request_id': 'stale', 'revision': row['revision']})
    with pytest.raises(MeasurementError, match='Request ID already used'):
        store.request_recheck(row['id'], {'request_id': 'recheck', 'revision': due['revision']})


def test_broker_validation_duplicate_case_and_concurrent_idempotency(tmp_path):
    _, store, person = setup_store(tmp_path)
    with pytest.raises(MeasurementError, match='HTTP'):
        store.create_broker({'request_id': 'bad', 'name': 'Bad', 'website': 'file:///etc/passwd', 'source': 'owner'})
    with pytest.raises(MeasurementError, match='HTTP'):
        store.create_broker({'request_id': 'credentials', 'name': 'Bad', 'website': 'https://user:secret@example.test', 'source': 'owner'})
    with pytest.raises(MeasurementError, match='boolean'):
        store.create_broker({'request_id': 'flag', 'name': 'Bad', 'website': 'https://example.test', 'source': 'owner', 'enabled': 'yes'})
    catalog = broker(store)
    created = case(store, person, catalog)
    with pytest.raises(MeasurementError, match='already exists'):
        case(store, person, catalog, 'duplicate')
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: PrivacyBrokerStore(tmp_path).record_user_observation(created['id'], {
            'request_id': 'concurrent-observation', 'revision': 1, 'outcome': 'found', 'evidence': 'Same owner observation',
        }), range(3)))
    assert rows[0] == rows[1] == rows[2]
    assert len(store.history(created['id'])) == 2
    assert store.get_case(created['id'])['revision'] == 2


@pytest.mark.asyncio
async def test_http_owner_journey_has_no_verified_or_send_route_and_is_home_scoped(tmp_path):
    app, isolated_app = web.Application(), web.Application()
    register(app, tmp_path / 'one')
    register(isolated_app, tmp_path / 'two')
    base = '/api/capabilities/wellbeing/privacy'
    async with TestClient(TestServer(app)) as client, TestClient(TestServer(isolated_app)) as isolated:
        privacy = PrivacyStore(tmp_path / 'one')
        person = privacy.create_subject({'request_id': 'subject', 'alias': 'Owner', 'relationship': 'self', 'source': 'owner'})
        for scope in ('broker_scan', 'broker_submit'):
            privacy.consent(person['id'], {'request_id': scope, 'revision': 0, 'scope': scope, 'granted': True, 'method': 'owner'})
        response = await client.post(base + '/brokers', json={'request_id': 'broker', 'name': 'HTTP Broker', 'website': 'https://broker.example', 'optout_url': '', 'source': 'owner'})
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        catalog = await response.json()
        cases_url = f"{base}/subjects/{person['id']}/broker-cases"
        response = await client.post(cases_url, json={'request_id': 'case', 'broker_id': catalog['id']})
        assert response.status == 200
        created = await response.json()
        response = await client.post(f"{base}/broker-cases/{created['id']}/observe", json={'request_id': 'observe', 'revision': 1, 'outcome': 'found', 'evidence': 'Owner saw HTTP result'})
        observed = await response.json()
        assert observed['state'] == 'found'
        assert observed['evidence_basis'] == 'user_attested'
        response = await client.post(f"{base}/broker-cases/{created['id']}/transition", json={'request_id': 'start', 'revision': 2, 'state': 'optout_in_progress', 'reason': 'Owner started elsewhere'})
        progressed = await response.json()
        assert progressed['state'] == 'optout_in_progress'
        response = await client.post(f"{base}/broker-cases/{created['id']}/recheck", json={'request_id': 'recheck', 'revision': 3})
        due = await response.json()
        assert due['state'] == 'optout_in_progress'
        assert len((await (await client.get(f"{base}/broker-cases/{created['id']}/history")).json())['history']) == 4
        assert len((await (await client.get(f"{base}/broker-cases/{created['id']}/events")).json())['events']) == 4
        listed = await (await client.get(cases_url)).json()
        assert listed['cases'][0]['broker']['name'] == 'HTTP Broker'
        assert (await isolated.get(f"{base}/broker-cases/{created['id']}")).status == 404
        assert (await client.post(f"{base}/broker-cases/{created['id']}/verified", json={})).status == 404
        assert (await client.post(f"{base}/broker-cases/{created['id']}/send", json={})).status == 404
        assert 'confirmed_removed' not in json.dumps(listed)
        assert 'verified_rescan' not in json.dumps(listed)


@pytest.mark.asyncio
async def test_http_reports_consent_conflict_stale_revision_and_unknown_case(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    privacy = PrivacyStore(tmp_path)
    person = privacy.create_subject({'request_id': 'subject', 'alias': 'Owner', 'relationship': 'self', 'source': 'owner'})
    base = '/api/capabilities/wellbeing/privacy'
    async with TestClient(TestServer(app)) as client:
        catalog = await (await client.post(base + '/brokers', json={'request_id': 'broker', 'name': 'Consent Broker', 'website': 'https://broker.example', 'source': 'owner'})).json()
        response = await client.post(f"{base}/subjects/{person['id']}/broker-cases", json={'request_id': 'case', 'broker_id': catalog['id']})
        assert response.status == 403
        assert (await response.json())['error']['code'] == 'consent_required'
        privacy.consent(person['id'], {'request_id': 'scan', 'revision': 0, 'scope': 'broker_scan', 'granted': True, 'method': 'owner'})
        created = await (await client.post(f"{base}/subjects/{person['id']}/broker-cases", json={'request_id': 'case-two', 'broker_id': catalog['id']})).json()
        observed = await (await client.post(f"{base}/broker-cases/{created['id']}/observe", json={'request_id': 'observe', 'revision': 1, 'outcome': 'found', 'evidence': 'Owner saw it'})).json()
        response = await client.post(f"{base}/broker-cases/{created['id']}/observe", json={'request_id': 'stale', 'revision': 1, 'outcome': 'blocked', 'evidence': 'Old tab'})
        assert response.status == 409
        assert (await response.json())['error']['code'] == 'conflict'
        response = await client.post(f"{base}/broker-cases/{observed['id']}/transition", json={'request_id': 'submit-denied', 'revision': 2, 'state': 'optout_in_progress', 'reason': ''})
        assert response.status == 403
        assert (await client.get(base + '/broker-cases/missing')).status == 404

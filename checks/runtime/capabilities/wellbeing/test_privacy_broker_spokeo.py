import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_broker_spokeo import register
from gideon.workspace.capabilities.wellbeing.privacy import PrivacyStore
from gideon.workspace.capabilities.wellbeing.privacy_broker_spokeo import (
    APPROVAL,
    SpokeoCaseAdapter,
    SpokeoProtocol,
)
from gideon.workspace.capabilities.wellbeing.privacy_brokers import PrivacyBrokerStore
from gideon.workspace.capabilities.wellbeing.privacy_brokers import BrokerAdapterResult
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def setup_case(home, origin, *, scan=True, submit=True):
    privacy = PrivacyStore(home)
    subject = privacy.create_subject({'request_id': 'subject', 'alias': 'Owner', 'relationship': 'self', 'source': 'owner'})
    for scope, granted in (('broker_scan', scan), ('broker_submit', submit)):
        privacy.consent(subject['id'], {'request_id': scope, 'revision': 0, 'scope': scope,
                                        'granted': granted, 'method': 'owner choice'})
    store = PrivacyBrokerStore(home)
    broker = store.create_broker({'request_id': 'broker', 'name': 'Spokeo', 'website': origin,
                                  'optout_url': origin + '/optout', 'source': 'curated protocol'})
    case = store.create_case(subject['id'], {'request_id': 'case', 'broker_id': broker['id']})
    return privacy, store, subject, case


async def contract_server(*, listed=True, form='valid'):
    state = {'listed': listed, 'posts': [], 'requests': []}
    app = web.Application()

    async def search(request):
        state['requests'].append(('GET', request.path))
        if not state['listed']:
            return web.Response(text='<html><h1>No results found</h1></html>')
        return web.Response(text='<html><article>Jane Doe lives in Oakland, CA</article></html>')

    async def optout(request):
        state['requests'].append(('GET', request.path))
        if form == 'captcha':
            return web.Response(text='<form action="/optout/submit" method="post"><div class="g-recaptcha"></div><input name="url"><input name="email"></form>')
        if form == 'changed':
            return web.Response(text='<form action="/new" method="get"><input name="profile"></form>')
        return web.Response(text='<form action="/optout/submit" method="post"><input type="hidden" name="csrf" value="contract-token"><input name="url"><input name="email"></form>')

    async def submit(request):
        state['requests'].append(('POST', request.path))
        state['posts'].append(dict(await request.post()))
        return web.Response(text='<html>Request received. Check your email for the confirmation email.</html>')

    app.router.add_get('/Jane-Doe/CA', search)
    app.router.add_get('/optout', optout)
    app.router.add_post('/optout/submit', submit)
    server = TestServer(app)
    await server.start_server()
    return server, state


def payload(request_id, revision, **values):
    return {'request_id': request_id, 'revision': revision, **values}


def identity():
    return {'first_name': 'Jane', 'last_name': 'Doe', 'city': 'Oakland', 'state': 'CA'}


@pytest.mark.asyncio
async def test_real_spokeo_html_protocol_drives_scan_prepare_submit_and_verified_rescan(tmp_path):
    server, contract = await contract_server()
    origin = str(server.make_url('')).rstrip('/')
    try:
        _, store, _, row = setup_case(tmp_path, origin)
        service = SpokeoCaseAdapter(store, SpokeoProtocol(origin, contract_mode=True))

        scanned = await service.scan(row['id'], payload('scan', 1, **identity()))
        assert scanned['state'] == 'found'
        assert scanned['revision'] == 2
        assert scanned['evidence_basis'] == 'provider_protocol'
        assert scanned['verifier'] == 'spokeo-html-v1'
        assert scanned['evidence'].startswith('sha256:')
        assert 'Jane' not in scanned['evidence']
        assert contract['posts'] == []

        prepared = await service.prepare(row['id'], payload(
            'prepare', 2, profile_url=origin + '/Jane-Doe/CA/record-1', email='jane@example.test'))
        assert prepared['case']['state'] == 'optout_in_progress'
        assert prepared['case']['revision'] == 3
        assert prepared['case']['evidence_basis'] == 'provider_protocol'
        assert prepared['plan']['method'] == 'POST'
        assert prepared['plan']['form_url'] == origin + '/optout/submit'
        assert prepared['plan']['disclosed_fields'] == ['listing_url', 'email']
        assert prepared['plan']['approval_phrase'] == APPROVAL
        assert prepared['plan']['live_submission_enabled'] is True
        assert contract['posts'] == []

        with pytest.raises(MeasurementError, match='approval phrase') as denied:
            await service.submit(row['id'], payload(
                'denied', 3, profile_url=origin + '/Jane-Doe/CA/record-1',
                email='jane@example.test', approval='yes'))
        assert denied.value.status == 403
        assert denied.value.code == 'approval_required'
        assert contract['posts'] == []
        assert store.get_case(row['id'])['state'] == 'optout_in_progress'

        submitted = await service.submit(row['id'], payload(
            'submit', 3, profile_url=origin + '/Jane-Doe/CA/record-1',
            email='jane@example.test', approval=APPROVAL))
        assert submitted['state'] == 'submitted'
        assert submitted['revision'] == 4
        assert submitted['evidence_basis'] == 'provider_protocol'
        assert len(contract['posts']) == 1
        assert contract['posts'][0] == {
            'csrf': 'contract-token', 'url': origin + '/Jane-Doe/CA/record-1',
            'email': 'jane@example.test'}

        present = await service.verify(row['id'], payload('verify-present', 4, **identity()))
        assert present['state'] == 'awaiting_processing'
        assert present['revision'] == 5
        assert present['evidence_basis'] == 'provider_protocol'
        contract['listed'] = False
        removed = await service.verify(row['id'], payload('verify-removed', 5, **identity()))
        assert removed['state'] == 'confirmed_removed'
        assert removed['revision'] == 6
        assert removed['verified_at']
        assert removed['next_recheck_at'] > removed['verified_at']

        assert [event['operation'] for event in store.events(row['id'])] == [
            'created', 'provider_scan', 'provider_optout_prepared',
            'provider_optout_submitted', 'provider_verified', 'provider_verified']
        assert [case['state'] for case in store.history(row['id'])] == [
            'unscanned', 'found', 'optout_in_progress', 'submitted',
            'awaiting_processing', 'confirmed_removed']
        encoded = json.dumps(store.events(row['id']))
        assert 'jane@example.test' not in encoded
        assert '/record-1' not in encoded
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_http_provider_routes_use_same_protocol_and_never_submit_during_prepare(tmp_path):
    provider, contract = await contract_server()
    origin = str(provider.make_url('')).rstrip('/')
    _, store, _, row = setup_case(tmp_path, origin)
    service = SpokeoCaseAdapter(store, SpokeoProtocol(origin, contract_mode=True))
    app = web.Application()
    register(app, adapter=service)
    base = f'/api/capabilities/wellbeing/privacy/broker-cases/{row["id"]}/providers/spokeo'
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.post(base + '/scan', json=payload('http-scan', 1, **identity()))
            assert response.status == 200
            assert response.headers['Cache-Control'] == 'no-store'
            scanned = await response.json()
            assert scanned['state'] == 'found'

            response = await client.post(base + '/prepare', json=payload(
                'http-prepare', 2, profile_url=origin + '/Jane-Doe/CA/id', email='owner@example.test'))
            assert response.status == 200
            prepared = await response.json()
            assert prepared['case']['state'] == 'optout_in_progress'
            assert prepared['plan']['approval_phrase'] == APPROVAL
            assert contract['posts'] == []

            response = await client.post(base + '/submit', json=payload(
                'http-denied', 3, profile_url=origin + '/Jane-Doe/CA/id',
                email='owner@example.test', approval='SUBMIT'))
            assert response.status == 403
            assert (await response.json())['error']['code'] == 'approval_required'
            assert contract['posts'] == []

            response = await client.post(base + '/submit', json=payload(
                'http-submit', 3, profile_url=origin + '/Jane-Doe/CA/id',
                email='owner@example.test', approval=APPROVAL))
            assert response.status == 200
            assert (await response.json())['state'] == 'submitted'
            assert len(contract['posts']) == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_consent_provider_identity_and_revision_are_checked_before_network(tmp_path):
    server, contract = await contract_server()
    origin = str(server.make_url('')).rstrip('/')
    try:
        privacy, store, subject, row = setup_case(tmp_path, origin, submit=False)
        service = SpokeoCaseAdapter(store, SpokeoProtocol(origin, contract_mode=True))
        with pytest.raises(MeasurementError, match='changed; reload'):
            await service.scan(row['id'], payload('stale', 0, **identity()))
        assert contract['posts'] == []
        scanned = await service.scan(row['id'], payload('scan', 1, **identity()))
        with pytest.raises(MeasurementError, match='broker_submit'):
            await service.prepare(row['id'], payload(
                'prepare', scanned['revision'], profile_url=origin + '/Jane-Doe/CA/id',
                email='owner@example.test'))
        assert contract['posts'] == []
        assert contract['requests'] == [('GET', '/Jane-Doe/CA')]

        privacy.consent(subject['id'], {'request_id': 'revoke-scan', 'revision': 1,
                                        'scope': 'broker_scan', 'granted': False,
                                        'method': 'owner revoked'})
        with pytest.raises(MeasurementError, match='broker_scan'):
            await service.scan(row['id'], payload('revoked', scanned['revision'], **identity()))
        assert contract['posts'] == []
        assert contract['requests'] == [('GET', '/Jane-Doe/CA')]

        other = store.create_broker({'request_id': 'other', 'name': 'Other Broker',
                                     'website': origin, 'source': 'owner'})
        privacy.consent(subject['id'], {'request_id': 'grant-scan', 'revision': 2,
                                        'scope': 'broker_scan', 'granted': True,
                                        'method': 'owner restored'})
        other_case = store.create_case(subject['id'], {'request_id': 'other-case', 'broker_id': other['id']})
        with pytest.raises(MeasurementError, match='Spokeo cases only'):
            await service.scan(other_case['id'], payload('wrong-provider', 1, **identity()))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_protocol_rejects_captcha_changed_form_cross_origin_and_live_local_origin(tmp_path):
    with pytest.raises(MeasurementError, match='requires https'):
        SpokeoProtocol('http://127.0.0.1:9')

    captcha_server, _ = await contract_server(form='captcha')
    captcha_origin = str(captcha_server.make_url('')).rstrip('/')
    try:
        protocol = SpokeoProtocol(captcha_origin, contract_mode=True)
        with pytest.raises(MeasurementError, match='human challenge') as captcha:
            await protocol.prepare(captcha_origin + '/Jane-Doe/CA/id', 'owner@example.test')
        assert captcha.value.code == 'human_required'
    finally:
        await captcha_server.close()

    changed_server, _ = await contract_server(form='changed')
    changed_origin = str(changed_server.make_url('')).rstrip('/')
    try:
        protocol = SpokeoProtocol(changed_origin, contract_mode=True)
        with pytest.raises(MeasurementError, match='protocol changed') as changed:
            await protocol.prepare(changed_origin + '/Jane-Doe/CA/id', 'owner@example.test')
        assert changed.value.status == 502
        with pytest.raises(MeasurementError, match='configured Spokeo origin'):
            await protocol.prepare('https://attacker.example/profile', 'owner@example.test')
        with pytest.raises(MeasurementError, match='email must be valid'):
            await protocol.prepare(changed_origin + '/profile', 'not-an-email')
    finally:
        await changed_server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(('listed', 'expected'), [(True, 'found'), (False, 'not_found')])
async def test_contract_scan_classifies_positive_and_negative_pages(listed, expected):
    server, _ = await contract_server(listed=listed)
    origin = str(server.make_url('')).rstrip('/')
    try:
        result = await SpokeoProtocol(origin, contract_mode=True).scan(identity())
        assert result.provider == 'spokeo-html-v1'
        assert result.outcome == expected
        assert result.checked_at.endswith('+00:00')
        assert result.evidence_ref.startswith('sha256:')
        assert len(result.evidence_ref) == 71
    finally:
        await server.close()


def test_store_adapter_boundaries_reject_untyped_results_and_invalid_states(tmp_path):
    _, store, _, row = setup_case(tmp_path, 'https://www.spokeo.com')
    mutation = {'request_id': 'typed', 'revision': row['revision']}
    with pytest.raises(MeasurementError, match='typed adapter result') as scan:
        store.apply_adapter_scan(row['id'], mutation, {'outcome': 'found'})
    assert scan.value.status == 403
    assert scan.value.code == 'verification_required'
    with pytest.raises(MeasurementError, match='typed adapter result') as prepared:
        store.apply_adapter_prepared(row['id'], mutation, {'outcome': 'prepared'})
    assert prepared.value.status == 403
    with pytest.raises(MeasurementError, match='typed adapter result') as submitted:
        store.apply_adapter_submission(row['id'], mutation, {'outcome': 'submitted'})
    assert submitted.value.status == 403
    with pytest.raises(MeasurementError, match='typed adapter result') as verified:
        store.apply_adapter_verification(row['id'], mutation, {'outcome': 'not_found'})
    assert verified.value.status == 403

    checked_at = '2026-09-25T12:00:00+00:00'
    found = BrokerAdapterResult('spokeo-html-v1', 'found', checked_at, 'sha256:' + 'a' * 64)
    scanned = store.apply_adapter_scan(row['id'], mutation, found)
    assert scanned['state'] == 'found'
    assert scanned['revision'] == 2
    assert scanned['evidence_basis'] == 'provider_protocol'
    assert scanned['evidence'] == 'sha256:' + 'a' * 64
    assert scanned['verified_at'] == checked_at
    assert scanned['verifier'] == 'spokeo-html-v1'

    with pytest.raises(MeasurementError, match='not ready for opt-out submission') as premature:
        store.apply_adapter_submission(row['id'], {'request_id': 'premature', 'revision': 2},
                                       BrokerAdapterResult('spokeo-html-v1', 'submitted', checked_at, 'sha256:' + 'b' * 64))
    assert premature.value.status == 409
    with pytest.raises(MeasurementError, match='not ready for provider verification') as premature_verify:
        store.apply_adapter_verification(row['id'], {'request_id': 'premature-verify', 'revision': 2},
                                         BrokerAdapterResult('spokeo-html-v1', 'not_found', checked_at, 'sha256:' + 'c' * 64))
    assert premature_verify.value.status == 409
    assert store.get_case(row['id']) == scanned
    assert len(store.history(row['id'])) == 2
    assert len(store.events(row['id'])) == 2
    assert store.events(row['id'])[-1]['operation'] == 'provider_scan'


@pytest.mark.asyncio
async def test_protocol_validates_identity_before_request_and_live_gate_before_form(tmp_path, monkeypatch):
    server, contract = await contract_server()
    origin = str(server.make_url('')).rstrip('/')
    try:
        protocol = SpokeoProtocol(origin, contract_mode=True)
        with pytest.raises(MeasurementError, match='two-letter'):
            await protocol.scan({'first_name': 'Jane', 'last_name': 'Doe', 'state': 'California'})
        assert contract['requests'] == []
        with pytest.raises(MeasurementError, match='Missing or unsupported'):
            await protocol.scan({'first_name': 'Jane', 'last_name': 'Doe', 'state': 'CA', 'dob': 'secret'})
        assert contract['requests'] == []

        monkeypatch.delenv('GIDEON_ALLOW_LIVE_SPOKEO_SUBMIT', raising=False)
        live = object.__new__(SpokeoProtocol)
        live.origin = origin
        live.contract_mode = False
        with pytest.raises(MeasurementError, match='Live Spokeo submission is disabled') as disabled:
            await live.submit(origin + '/Jane-Doe/CA/id', 'owner@example.test', APPROVAL)
        assert disabled.value.status == 403
        assert disabled.value.code == 'approval_required'
        assert contract['requests'] == []
        assert contract['posts'] == []
    finally:
        await server.close()

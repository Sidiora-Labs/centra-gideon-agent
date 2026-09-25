import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.core.config.loader import config_dir, config_path
from gideon.integrations.llm.branded_specs import BrandedProviderSpec
from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.registry import ProviderRegistry, ProviderEntry, get_default_registry, set_default_registry
from gideon.integrations.llm.events import AgentEvent
from gideon.sdk.provider_helpers import register_branded_app
from gideon.operations import usage_ledger as ledger
from gideon.operations.durability.shards import machine_id
from gideon.interfaces.dashboard.handlers.capabilities_accounting import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware, generate_token, use_ephemeral_secret
from gideon.workspace.capabilities.platform.accounting import view
from gideon.workspace.capabilities.platform.tools import create_provider

PREFIX = '/api/capabilities/platform/accounting'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    monkeypatch.delenv('GIDEON_DEV_NO_AUTH', raising=False)
    monkeypatch.delenv('GIDEON_BYPASS_LOCAL_NETWORKS', raising=False)
    use_ephemeral_secret()
    previous = get_default_registry()
    set_default_registry(ProviderRegistry())
    register_branded_app(BrandedProviderSpec(type='accounting-api', protocol='openai'))
    register_branded_app(BrandedProviderSpec(type='accounting-subscription', protocol='openai', credential_source='declared-subscription'))
    CredentialStore(tmp_path).save({'billing-a': {'type': 'static_token', 'value': 'never-return-this-secret'}, 'billing-b': {'type': 'static_token', 'value': 'another-private-secret'}})
    yield tmp_path
    set_default_registry(previous)


def entry(name='work', credential='billing-a', provider_type='accounting-api', options=None):
    registry = get_default_registry()
    registry.unregister_entry(name)
    value = ProviderEntry(name=name, type=provider_type, model='model-a', credential=credential, options=options or {})
    registry.register_entry(value)
    return value


def emit(provider='work', tokens=100, cost=.25, source='chat'):
    event = AgentEvent(kind='complete', input_tokens=tokens, output_tokens=20, cache_read_tokens=3, cache_creation_tokens=4, cost_usd=cost, duration_ms=1000)
    ledger.record_from_event(event, source=source, session_key='dashboard:accounting', provider=provider, model='model-a', estimate_if_missing=False)


def test_actual_event_writer_captures_instance_binding_without_secret(home):
    entry()
    emit()
    rows = ledger._iter_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row['instance_id'] == machine_id(home)
    assert row['provider_instance'] == 'work'
    assert row['credential_ref'] == 'billing-a'
    assert row['subscription_source'] is None
    assert row['attribution'] == 'binding_at_emission'
    assert row['input_tokens'] == 100
    assert row['output_tokens'] == 20
    assert row['cache_read_tokens'] == 3
    assert row['cache_creation_tokens'] == 4
    assert row['duration_ms'] == 1000
    assert row['cost_usd'] == .25
    text = ledger._path().read_text()
    assert 'never-return-this-secret' not in text
    assert 'another-private-secret' not in text
    assert (home / 'machine_id').read_text().strip() == row['instance_id']
    assert ledger.totals()['input_tokens'] == 100


def test_credential_rotation_preserves_historical_binding_and_groups(home):
    entry()
    emit(tokens=100)
    first = ledger._path().read_text().splitlines()[0]
    entry(credential='billing-b')
    emit(tokens=200, cost=.5)
    assert ledger._path().read_text().splitlines()[0] == first
    result = view()
    assert result['turns'] == 2
    assert result['unattributed_turns'] == 0
    assert len(result['rows']) == 2
    bindings = {row['credential_ref']: row for row in result['rows']}
    assert bindings['billing-a']['input_tokens'] == 100
    assert bindings['billing-b']['input_tokens'] == 200
    assert bindings['billing-a']['recorded_cost_usd'] == .25
    assert bindings['billing-b']['recorded_cost_usd'] == .5
    assert all(row['billed_cost_usd'] is None for row in result['rows'])
    assert all(row['quota_remaining'] is None for row in result['rows'])
    get_default_registry().unregister_entry('work')
    assert view()['rows'] == result['rows']


def test_shared_connection_reference_snapshotted_at_real_write_boundary(home):
    config_path().write_text(json.dumps({'providers': [], 'provider_connections': {'shared': {'label': 'Shared account', 'base_url': 'https://api.example.invalid/v1', 'credential_ref': 'billing-a', 'model_access': {'mode': 'all', 'patterns': []}, 'revision': 1}}}))
    entry(credential=None, options={'connection_id': 'shared'})
    emit()
    first = ledger._iter_rows()[0]
    assert first['credential_ref'] == 'billing-a'
    document = json.loads(config_path().read_text())
    document['provider_connections']['shared']['credential_ref'] = 'billing-b'
    config_path().write_text(json.dumps(document))
    emit()
    assert [row['credential_ref'] for row in ledger._iter_rows()] == ['billing-a', 'billing-b']
    assert all(row['provider_instance'] == 'work' for row in ledger._iter_rows())
    assert 'base_url' not in json.dumps(view())


def test_declared_subscription_binding_and_explicit_credential_precedence(home):
    entry(credential=None, provider_type='accounting-subscription')
    emit()
    first = ledger._iter_rows()[0]
    assert first['subscription_source'] == 'declared-subscription'
    assert first['credential_ref'] is None
    entry(credential='billing-a', provider_type='accounting-subscription')
    emit()
    assert ledger._iter_rows()[1]['subscription_source'] is None
    assert ledger._iter_rows()[1]['credential_ref'] == 'billing-a'
    entry(credential=None, provider_type='accounting-subscription', options={'api_key': 'inline-secret'})
    emit()
    assert ledger._iter_rows()[2]['subscription_source'] is None
    assert 'inline-secret' not in ledger._path().read_text()


def test_unknown_provider_and_legacy_history_never_backfilled(home):
    legacy = {'ts': datetime.now(timezone.utc).isoformat(), 'provider': 'work', 'model': 'model-a', 'source': 'cli', 'input_tokens': 9, 'output_tokens': 3, 'priced': False}
    ledger._path().parent.mkdir(parents=True)
    raw = json.dumps(legacy)
    ledger._path().write_text(raw + '\n')
    entry()
    emit(provider='acp', cost=0)
    rows = ledger._iter_rows()
    assert 'instance_id' not in rows[0]
    assert rows[1]['instance_id'] == machine_id(home)
    assert rows[1]['provider_instance'] is None
    assert rows[1]['credential_ref'] is None
    assert rows[1]['attribution'] == 'instance_only'
    assert ledger._path().read_text().splitlines()[0] == raw
    result = view()
    assert result['turns'] == 2
    assert result['unattributed_turns'] == 2
    assert len(result['rows']) == 2
    assert sum(row['unpriced_turns'] for row in result['rows']) == 2
    assert 'Legacy attribution stays unknown' in result['coverage']
    assert result['rows'][0]['billed_cost_usd'] is None


def test_allocation_instance_identity_is_scoped_to_real_home(home, tmp_path, monkeypatch):
    entry()
    emit()
    first = ledger._iter_rows()[0]['instance_id']
    other = tmp_path / 'other-allocation'
    other.mkdir()
    monkeypatch.setenv('GIDEON_HOME', str(other))
    emit()
    second = ledger._iter_rows()[0]['instance_id']
    assert first != second
    assert second == machine_id(other)
    assert len(ledger._iter_rows()) == 1
    assert ledger._iter_rows()[0]['provider_instance'] == 'work'
    assert len((home / 'usage/turns.jsonl').read_text().splitlines()) == 1


def test_window_invalid_rows_unknown_prices_and_source_preservation(home):
    entry()
    now = datetime.now(timezone.utc)
    ledger.record_turn(ledger.TurnUsage(ts=(now - timedelta(days=10)).isoformat(), session_key='session', source='loop', agent='', provider='work', model='model-a', input_tokens=5, cost_usd=0, priced=False))
    emit(source='chat')
    before = ledger._path().read_bytes()
    assert view(days=1)['turns'] == 1
    result = view(days=30)
    assert result['turns'] == 2
    assert result['rows'][0]['unpriced_turns'] == 1
    assert result['rows'][0]['sources'] == ['chat', 'loop']
    assert ledger._path().read_bytes() == before
    with ledger._path().open('a') as stream:
        stream.write(json.dumps({'ts': 'not-a-time'}) + '\n')
        stream.write(json.dumps({'ts': now.isoformat(), 'input_tokens': -1}) + '\n')
    assert view()['invalid_rows'] == 2
    assert view()['turns'] == 2
    for invalid in (0, 366, True, '30'):
        with pytest.raises(ValueError):
            view(days=invalid)


@pytest.mark.asyncio
async def test_signed_http_and_native_read_canonical_accounting(home):
    entry()
    emit()
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    before = ledger._path().read_bytes()
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(PREFIX)).status in {401, 403}
        response = await client.get(PREFIX, params={'token': generate_token('owner')})
        assert response.status == 200
        result = await response.json()
        assert result['rows'][0]['credential_ref'] == 'billing-a'
        assert result['rows'][0]['input_tokens'] == 100
        assert (await client.get(PREFIX, params={'days': 'bad'})).status == 400
        assert (await client.post(PREFIX, json={})).status == 405
        provider = create_provider()
        native = await provider.invoke('platform_usage_accounting', {'days': 1})
        assert native.success
        assert json.loads(native.output)['turns'] == 1
        tool = next(item for item in await provider.list_tools() if item.name == 'platform_usage_accounting')
        assert not tool.requires_approval
        manifest = json.loads(Path('runtime/gideon/extensions/apps/native/gideon-platform/app.json').read_text())
        assert tool.name in manifest['provider']['capabilities']
    assert ledger._path().read_bytes() == before

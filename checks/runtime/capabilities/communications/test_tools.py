import asyncio
import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from gideon.integrations.tool_providers.base import RiskLevel, ToolProvider
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.capabilities.communications.tools import create_provider


@pytest.fixture
def isolated_home(tmp_path):
    previous = os.environ.get('GIDEON_HOME')
    os.environ['GIDEON_HOME'] = str(tmp_path)
    try:
        yield tmp_path
    finally:
        if previous is None:
            os.environ.pop('GIDEON_HOME', None)
        else:
            os.environ['GIDEON_HOME'] = previous


def call(provider, name, arguments):
    return asyncio.run(provider.invoke(name, arguments))


def output(result):
    assert result.success, result.error
    return json.loads(result.output)


def test_native_manifest_resolves_real_tool_provider(isolated_home):
    root = Path(__file__).resolve().parents[4]
    manifest = json.loads((root / 'runtime/gideon/extensions/apps/native/gideon-people/app.json').read_text())
    module, factory = manifest['provider']['implementation'].split(':')
    provider = getattr(importlib.import_module(module), factory)({})
    assert isinstance(provider, ToolProvider)
    assert manifest['name'] == provider.name == 'gideon-people'
    assert manifest['native'] is True
    assert manifest['provider']['type'] == 'tool'
    definitions = asyncio.run(provider.list_tools())
    by_name = {tool.name: tool for tool in definitions}
    reads = {'people_list', 'people_get', 'people_import_preview', 'people_care_report'}
    writes = {'people_create', 'people_update', 'people_record_touchpoint', 'people_import_commit', 'people_record_thread_evidence'}
    reads |= {'people_mirror_accounts', 'people_mirror_capabilities', 'people_mirror_messages'}
    writes |= {'people_mirror_create', 'people_mirror_update', 'people_mirror_upload', 'people_mirror_sync'}
    reads |= {'people_desktop_preview', 'people_desktop_imports', 'people_desktop_history'}
    writes |= {'people_desktop_commit'}
    reads |= {'people_beeper_settings', 'people_beeper_page', 'people_beeper_asset', 'people_beeper_outbox'}
    writes |= {'people_beeper_configure', 'people_beeper_refresh', 'people_beeper_asset_fetch', 'people_beeper_draft', 'people_beeper_send', 'people_beeper_reconcile', 'people_beeper_discard', 'people_beeper_recover'}
    reads |= {'people_telegram_config', 'people_telegram_command', 'people_telegram_deliveries'}
    writes |= {'people_telegram_configure', 'people_telegram_queue', 'people_telegram_send'}
    reads |= {'people_calendar_sources', 'people_calendar_daily'}
    writes |= {'people_calendar_create', 'people_calendar_update', 'people_calendar_upload', 'people_calendar_sync'}
    reads |= {'people_activity_timeline', 'people_platform_agents', 'people_platform_assignments', 'people_platform_assignment_get', 'people_platform_assignment_history', 'people_stacker_territories', 'people_stacker_actions', 'people_x_snapshot', 'people_x_drafts', 'people_social_accounts', 'people_social_get', 'people_social_history'}
    writes |= {'people_platform_assignment_create', 'people_platform_assignment_change', 'people_stacker_read', 'people_stacker_prepare', 'people_stacker_review', 'people_stacker_submit', 'people_stacker_reconcile', 'people_x_sync', 'people_x_draft_create', 'people_x_draft_update', 'people_x_review', 'people_social_create', 'people_social_update', 'people_social_remove'}
    assert set(by_name) == reads | writes
    for name, tool in by_name.items():
        assert tool.provider == 'gideon-people'
        assert tool.requires_approval is (name in writes)
        assert tool.risk_level == (RiskLevel.DESTRUCTIVE if name in ('people_stacker_submit', 'people_beeper_send', 'people_telegram_send', 'people_telegram_configure') else RiskLevel.CAUTION if name in writes else RiskLevel.SAFE)
        assert tool.interactive is False
        Draft202012Validator.check_schema(tool.parameters)
        assert tool.parameters['additionalProperties'] is False
    assert provider.connected is True
    assert provider.info()['name'] == 'gideon-people'


def test_actual_tools_create_read_update_and_touchpoint(isolated_home):
    provider = create_provider()
    assert output(call(provider, 'people_list', {})) == {'people': []}
    created = output(call(provider, 'people_create', {'person': {'name': 'Tool Friend', 'notes': 'Initial'}}))['person']
    assert created['revision'] == 1
    assert PeopleStore().get(created['id']) == created
    read = output(call(provider, 'people_get', {'person_id': created['id'], 'timezone': 'Europe/Berlin'}))
    assert read['person']['notes'] == 'Initial'
    assert read['touchpoints'] == []
    assert read['care']['state'] == 'missing'
    updated = output(call(provider, 'people_update', {'person_id': created['id'], 'person': {'name': 'Tool Friend', 'notes': 'Updated', 'revision': 1}}))['person']
    assert updated['revision'] == 2
    assert updated['notes'] == 'Updated'
    stale = call(provider, 'people_update', {'person_id': created['id'], 'person': {'name': 'Stale', 'revision': 1}})
    assert stale.success is False
    assert stale.metadata['status'] == 409
    assert stale.recovery_hints
    touch = {'source': 'manual', 'external_id': 'tool-contact', 'occurred_at': '2026-09-01T10:00:00Z', 'direction': 'mutual', 'summary': 'A call'}
    recorded = output(call(provider, 'people_record_touchpoint', {'person_id': created['id'], 'touchpoint': touch}))
    assert recorded['created'] is True
    replay = output(call(provider, 'people_record_touchpoint', {'person_id': created['id'], 'touchpoint': touch}))
    assert replay['created'] is False
    assert replay['touchpoint'] == recorded['touchpoint']
    people = output(call(provider, 'people_list', {}))['people']
    assert len(people) == 1
    assert people[0]['name'] == 'Tool Friend'
    assert people[0]['care']['last_contact'] == '2026-09-01T10:00:00+00:00'


def test_actual_tools_preview_and_commit_contacts(isolated_home):
    provider = create_provider()
    data = {'format': 'csv', 'content': 'name,email\nImported,imported@example.com'}
    projected = output(call(provider, 'people_import_preview', data))
    assert projected['rows'][0]['candidate']['name'] == 'Imported'
    assert PeopleStore().people() == []
    payload = {**data, 'source_digest': projected['source_digest'], 'decisions': [{'row_id': '1', 'action': 'create'}]}
    saved = output(call(provider, 'people_import_commit', payload))
    assert saved['created'] is True
    person_id = saved['receipt']['rows'][0]['person_id']
    assert PeopleStore().get(person_id)['identities'] == [{'kind': 'email', 'value': 'imported@example.com'}]
    replay = output(call(provider, 'people_import_commit', payload))
    assert replay['receipt'] == saved['receipt']
    assert replay['created'] is False
    assert len(PeopleStore().people()) == 1


def test_actual_tools_record_and_report_coverage(isolated_home):
    provider = create_provider()
    person = output(call(provider, 'people_create', {'person': {'name': 'Thread Friend'}}))['person']
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=2)
    payload = {'source': 'local-archive', 'source_account_id': 'provider-account', 'captured_at': now.isoformat(),
               'coverage_start': start.isoformat(), 'coverage_end': now.isoformat(),
               'incoming_complete': True, 'outgoing_complete': False,
               'messages': [{'person_id': person['id'], 'thread_id': 'one', 'external_id': 'one',
                             'occurred_at': start.isoformat(), 'direction': 'inbound', 'summary': 'Question'}]}
    saved = output(call(provider, 'people_record_thread_evidence', payload))
    assert saved['created'] is True
    assert saved['receipt']['inserted'] == 1
    assert saved['receipt']['qualification'] == 'recorded_evidence_only'
    report = output(call(provider, 'people_care_report', {'timezone': 'Europe/Berlin'}))
    assert report['threads'][0]['state'] == 'unknown'
    assert report['threads'][0]['source_account_id'] == 'provider-account'
    assert report['threads'][0]['person_id'] == person['id']
    assert report['people'][0]['care']['state'] == 'current'
    assert report['timezone'] == 'Europe/Berlin'
    person_detail = output(call(provider, 'people_get', {'person_id': person['id']}))
    person_list = output(call(provider, 'people_list', {}))
    assert person_detail['care']['state'] == 'current'
    assert person_list['people'][0]['care']['state'] == 'current'
    assert person_detail['touchpoints'] == []
    assert output(call(provider, 'people_record_thread_evidence', payload))['created'] is False


@pytest.mark.parametrize('name,args', [
    ('missing_tool', {}), ('people_get', {}), ('people_get', {'person_id': []}),
    ('people_create', {'person': {'name': 'Name', 'root': '/tmp'}}),
    ('people_list', {'home': '/tmp'}), ('people_list', {'timezone': 'Bad/Zone'}),
    ('people_import_preview', {'format': 'bad', 'content': 'data'}),
    ('people_import_commit', {'format': 'csv', 'content': 'name\nOne'}),
    ('people_update', {'person_id': 'missing', 'person': {'name': 'Name'}}),
    ('people_record_touchpoint', {'person_id': 'missing', 'touchpoint': {}}),
    ('people_record_thread_evidence', {'source_account_id': 'other-home'}),
    ('people_care_report', {'timezone': []}),
])
def test_invalid_tool_calls_are_structured_failures(isolated_home, name, args):
    result = call(create_provider(), name, args)
    assert result.success is False
    assert result.error
    assert result.metadata['status'] == 400
    assert result.recovery_hints
    assert PeopleStore().people() == []


def test_provider_configuration_cannot_select_another_home(isolated_home):
    other = isolated_home / 'other-runtime'
    provider = create_provider({'root': str(other), 'home': str(other)})
    person = output(call(provider, 'people_create', {'person': {'name': 'Current runtime'}}))['person']
    assert PeopleStore().get(person['id'])['name'] == 'Current runtime'
    assert not other.exists()
    missing = call(provider, 'people_get', {'person_id': 'missing'})
    assert missing.success is False
    assert missing.metadata['status'] == 404
    os.environ['GIDEON_HOME'] = str(other)
    assert output(call(provider, 'people_list', {}))['people'] == []
    os.environ['GIDEON_HOME'] = str(isolated_home)
    assert output(call(provider, 'people_get', {'person_id': person['id']}))['person']['name'] == 'Current runtime'

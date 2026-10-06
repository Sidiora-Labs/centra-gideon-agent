"""Real primary writer, app summary authorization, and consent-scoped read union."""
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from gideon.cognition.context import PromptAssembler
from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_record import MemoryRecord as GideonRecord, MemoryKind
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.config import DaemonConfig, DaemonTransport, LocalAuthConfig, LocalAuthMethod
from gideon.hypermid.contracts import RecordDraft, RecordKind, MemoryOperation, MutationRequest, RevisionPrecondition
from gideon.hypermid.enrollment_source import NativeEnrollmentSource
from gideon.hypermid.foundation import Scope, Id, Cursor, Digest
from gideon.hypermid.history import JournalRange
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.memory import HypermidMemoryProvider, _trace, install_as_memory_authority, uninstall_memory_authority
from gideon.security.session_credentials import begin_turn, end_turn
from gideon.security.approval_answer import YOU, app


@pytest.mark.asyncio
async def test_real_primary_app_scope_and_shared_union(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    for name in ('alpha-app', 'beta-app'):
        folder = tmp_path / 'apps' / name
        folder.mkdir(parents=True)
        (folder / 'app.json').write_text(json.dumps({'name': name, 'version': '1.0.0', 'permissions': {'agent': 'tools', 'memory': 'app-scoped'}}))
        (folder / 'installed.json').write_text(json.dumps({'name': name, 'version': '1.0.0', 'enabled': True, 'origin': 'local', 'tier': 'community'}))
    scope = Scope('union-owner', 'union-project', 'host-workspace')
    grant = NativeEnrollmentSource().issue(scope=scope)
    enrollment = LocalEnrollment(scope, Id('union-credential'), Id('union-capability'), tuple(grant['operations']), tuple(Id(value) for value in grant['resources']), grant['expires_ms'])
    root = tmp_path / 'hypermid' / 'runtime'
    record = root / 'connection.json'
    client = HypermidClient(record, scope=scope)
    journal = MemoryJournal(workspace=tmp_path/'memory-journal')
    journal.init()
    log = ConversationLog(base_dir=tmp_path/'sessions')
    log.append('alpha-origin', 'user', 'Historical constellation request')
    log.append('alpha-origin', 'assistant', 'The prior answer is recorded here.')
    query = 'constellation'
    log.append('alpha-origin', 'user', query)
    consolidator = HistoryConsolidator(log, journal)
    lifecycle = HypermidLifecycle(HypermidAdapter(client, mode='primary'), DaemonConfig(transport=DaemonTransport.UNIX_SOCKET, endpoint=str(root/'union.sock'), executable=str(Path('target/debug/hypermid-daemon').resolve()), start_on_demand=True, auth=LocalAuthConfig(method=LocalAuthMethod.PEER_AND_HMAC, token_file=str(root/'auth.token'), require_peer_identity=True)).with_connection_record(str(record)), connection_record=record, enrollment=enrollment, runtime=SimpleNamespace(ctx_builder=SimpleNamespace(memory=journal), conv_log=log, consolidator=consolidator))
    provider = None
    service = None
    credential = None
    try:
        status = await lifecycle.start()
        assert status.available
        await lifecycle._apply_mode('primary', activate_primary=True)
        assert lifecycle.writer.snapshot().owns_writes
        provider = HypermidMemoryProvider(record, scope=scope, capability_id=enrollment.capability_id, writer=lifecycle.writer)
        service = install_as_memory_authority(provider)
        provider.put([GideonRecord('owner-fact', MemoryKind.SEMANTIC, text='owner constellation shared fact', value='owner constellation shared fact')])
        for name in ('beta-app', 'alpha-app'):
            credential = begin_turn(name+'-origin', YOU, turn_id=name+'-turn', memory_mode='persistent', created_by_app=name, work_actor=app(name))
            assert provider._writes_allowed()
            provider.put([GideonRecord('same-key', MemoryKind.SEMANTIC, text=name+' constellation scoped fact', value=name+' constellation scoped fact')])
            assert provider.get('same-key').text.startswith(name)
            assert len(provider.query()) == 1
            if name == 'beta-app':
                end_turn(credential)
                credential = None
        alpha = provider._app_receipt()
        bridge = lifecycle.context_bridge
        receipt = bridge.sync_session('alpha-origin')
        source_digest = bridge.journal('alpha-origin').source_digest(JournalRange(Cursor(1, 1), Cursor(1, 2)))
        summary_text = 'Alpha scoped source summary'
        tiers = [{'level': level, 'content': summary_text, 'content_digest': str(Digest.sha256(summary_text.encode())), 'token_mass': 5} for level in range(4)]
        metadata = {'session_id': str(receipt.session_id), 'source_start': Cursor(1, 1).to_wire(), 'source_end': Cursor(1, 2).to_wire(), 'source_digest': str(source_digest), 'tiers': tiers, 'usage': {'provider': 'controlled-test', 'model': 'controlled-test', 'input_tokens': 10, 'output_tokens': 20, 'duration_ms': 1}}
        summary_id = Id('alpha-native-summary')
        request = MutationRequest(MemoryOperation.CREATE, scope, alpha.scope, RevisionPrecondition.must_not_exist(), _trace(), record_id=summary_id, category='context_summary')
        draft = RecordDraft(summary_id, alpha.scope, RecordKind.SUMMARY, 'context_summary', summary_text, 1.0, 1.0, metadata=metadata, summary={'input_set_digest': str(source_digest), 'level': 'standard', 'decay_half_life_ms': None})
        provider._call(lambda memory: memory.create(request, draft, now_ms=int(time.time()*1000), authority_resource=Id('memory-records')), target_scope=alpha.scope)
        builder = PromptAssembler(memory=provider, conversation_log=log)
        first = lifecycle.primary_engine.assemble(builder, query, is_new_session=False, session_key='alpha-origin')
        assert first.metadata['hypermid']['summary']['authorized_records'] == 1
        assert first.metadata['hypermid']['summary']['selected_records'] == 1
        assert summary_text in first.message
        assert 'owner constellation shared fact' not in first.message
        assert 'beta-app constellation scoped fact' not in first.message
        end_turn(credential)
        credential = None
        # A different session's authorized summary must not become this conversation's history.
        owner_metadata = dict(metadata, session_id='different-session')
        owner_summary = Id('owner-native-summary')
        owner_request = MutationRequest(MemoryOperation.CREATE, scope, scope, RevisionPrecondition.must_not_exist(), _trace(), record_id=owner_summary, category='context_summary')
        owner_text = 'Other conversation summary must stay separate'
        owner_metadata['tiers'] = [dict(tier, content=owner_text, content_digest=str(Digest.sha256(owner_text.encode()))) for tier in tiers]
        provider._call(lambda memory: memory.create(owner_request, RecordDraft(owner_summary, scope, RecordKind.SUMMARY, 'context_summary', owner_text, 1.0, 1.0, metadata=owner_metadata, summary={'input_set_digest': str(source_digest), 'level': 'standard', 'decay_half_life_ms': None}), now_ms=int(time.time()*1000), authority_resource=Id('memory-records')))
        manifest = tmp_path/'apps'/'alpha-app'/'app.json'
        changed = json.loads(manifest.read_text())
        changed['permissions']['memory'] = 'shared'
        manifest.write_text(json.dumps(changed))
        from gideon.extensions.apps import app_manager
        assert app_manager.enable('alpha-app')
        credential = begin_turn('alpha-origin', YOU, turn_id='alpha-shared-turn', memory_mode='persistent', created_by_app='alpha-app', work_actor=app('alpha-app'))
        renewed = provider._app_receipt()
        assert renewed.scope == alpha.scope and renewed.epoch == alpha.epoch+1
        assert provider.get('same-key').text == 'alpha-app constellation scoped fact'
        rows = provider.query()
        assert any(row.text == 'owner constellation shared fact' for row in rows)
        assert any(row.text == 'alpha-app constellation scoped fact' for row in rows)
        assert not any('beta-app' in row.text for row in rows)
        assert all(row.extra['native_scope_digest'] for row in rows)
        provider.put([GideonRecord('shared-new', MemoryKind.SEMANTIC, text='new owner constellation fact', value='new owner constellation fact')])
        assert provider.get('shared-new').extra['native_scope'] == scope.to_wire()
        recalled = provider.search_with_evidence('constellation')
        assert len(recalled.scope_cursors) == 2
        assert {tuple(source['scope'].items()) for source in recalled.scope_cursors} == {tuple(alpha.scope.to_wire().items()), tuple(scope.to_wire().items())}
        assert not any('beta-app' in hit.content for hit in recalled.hits)
        assert provider.get(recalled.hits[0].id) is not None
        second = lifecycle.primary_engine.assemble(builder, query, is_new_session=False, session_key='alpha-origin')
        summary = second.metadata['hypermid']['summary']
        assert summary['authorized_records'] == 2 and len(summary['source_cursors']) == 2
        assert summary_text in second.message
        assert owner_text not in second.message
        assert 'owner constellation shared fact' in second.message
        assert 'beta-app constellation scoped fact' not in second.message
        assert len(second.metadata['hypermid']['recall']['scope_cursors']) == 2
    finally:
        if credential:
            end_turn(credential)
        if service:
            uninstall_memory_authority(service)
        if provider:
            provider.close()
        await lifecycle.stop()

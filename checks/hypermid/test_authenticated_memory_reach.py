"""Live daemon admission intersects active work provenance and native authority."""
import contextvars
import json

import pytest

from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.contracts import RecordKind, MemoryOperation
from gideon.hypermid.memory import HypermidMemoryProvider
from gideon.security.approval_answer import Principal, OWNER, APP, CHANNEL, RUN
from gideon.security.session_credentials import begin_turn, end_turn, current_work, memory_reach
from test_memory_release import _start_daemon, _open_memory, _draft, _mutation


@pytest.mark.asyncio
async def test_live_native_record_admission_and_revoked_copied_context(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    scope = Scope('reach-owner', 'reach-project', 'reach-workspace')
    capability = Id('reach-capability')
    record_id = Id('reach-record')
    daemon = _start_daemon(tmp_path, 'daemon', scope, capability,
                           {record_id, Id('memory-service'), Id('memory-records')})
    client = None
    provider = HypermidMemoryProvider(daemon.record, scope=scope, capability_id=capability)
    try:
        client, memory = await _open_memory(daemon, scope, capability)
        draft = _draft(scope, record_id, RecordKind.FACT, 'private canonical fact')
        await memory.create(_mutation(scope, MemoryOperation.CREATE, record_id, 'create'), draft, now_ms=1)
        provider.init()
        assert provider.get(str(record_id)).text == 'private canonical fact'
        for actor, mode, permitted in [(Principal(OWNER), 'persistent', True),
                                       (Principal(OWNER), 'incognito', True),
                                       (Principal(OWNER), 'temporary', False),
                                       (Principal(CHANNEL, 'external'), 'persistent', False)]:
            credential = begin_turn('dashboard:reach', actor, turn_id=mode, memory_mode=mode)
            try:
                assert (provider.get(str(record_id)) is not None) == permitted
                reach = memory_reach(scope)
                assert reach.scope == scope
                assert reach.write_allowed == (actor.kind == OWNER and mode == 'persistent')
                assert reach.background_allowed == reach.write_allowed
            finally:
                end_turn(credential)
        credential = begin_turn('dashboard:reach', Principal(OWNER), turn_id='revoked', memory_mode='persistent')
        captured = contextvars.copy_context()
        end_turn(credential)
        assert captured.run(current_work) is None
        assert captured.run(provider.get, str(record_id)) is None
        # Source owner and effective workflow actor remain separate.
        credential = begin_turn('dashboard:reach', Principal(OWNER), turn_id='run', memory_mode='persistent',
                                work_actor=Principal(RUN, 'actual-run'))
        try:
            assert provider.get(str(record_id)).text == 'private canonical fact'
        finally:
            end_turn(credential)
    finally:
        provider.close()
        if client is not None:
            await client.close()
        daemon.stop()


def test_live_app_manifest_revocation_and_task_only(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    folder = tmp_path / 'apps' / 'reach-app'
    folder.mkdir(parents=True)
    installed = folder / 'installed.json'
    installed.write_text(json.dumps({'name': 'reach-app', 'enabled': True, 'version': '1.0.0'}))
    manifest = folder / 'app.json'
    def grant(tier, memory):
        manifest.write_text(json.dumps({'name': 'reach-app', 'version': '1.0.0',
                                        'permissions': {'agent': tier, 'memory': memory}}))
    grant('read', 'shared')
    scope = Scope('reach-owner', 'reach-project', 'reach-workspace')
    credential = begin_turn('app-run:reach', Principal(OWNER), turn_id='app', created_by_app='reach-app',
                            memory_mode='persistent', work_actor=Principal(APP, 'reach-app'))
    try:
        assert memory_reach(scope).read_allowed
        grant('text', 'shared')
        assert memory_reach(scope).reason == 'app_task_only'
        grant('read', 'app-scoped')
        assert memory_reach(scope).reason == 'app_shared_memory_not_granted'
        grant('read', 'shared')
        assert memory_reach(scope).read_allowed
        installed.write_text(json.dumps({'name': 'reach-app', 'enabled': False, 'version': '1.0.0'}))
        assert not memory_reach(scope).read_allowed
    finally:
        end_turn(credential)


def test_primary_denial_uses_real_default_context_without_native_recall(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(tmp_path)
    from gideon.cognition.context import PromptAssembler
    from gideon.hypermid.primary_engine import PrimaryContextEngine, ThreadedPrimaryBridge
    from gideon.hypermid.foundation import Trace
    scope = Scope('reach-owner', 'reach-project', 'reach-workspace')
    bridge = ThreadedPrimaryBridge(tmp_path / 'no-authority.json', scope)
    engine = PrimaryContextEngine(bridge, summary_capability_id=Id('summary-capability'))
    builder = PromptAssembler()
    credential = begin_turn('dashboard:reach', Principal(CHANNEL, 'external'), turn_id='foreign', memory_mode='persistent')
    try:
        result = engine.assemble(builder, 'only current request', is_new_session=False, session_key='dashboard:reach')
        assert 'only current request' in result.message
        assert builder.resolve_scoped_recall('private fact', scope=scope).reason == 'foreign_origin'
        payload = engine._request_payload(session_id='real-session', new_items=[],
            trace=Trace(Id('reach-trace'), Id('reach-request')), writer_lease={})
        assert payload['summary_access'] is None
    finally:
        end_turn(credential)
        bridge._executor.shutdown()

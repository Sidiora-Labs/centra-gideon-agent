"""Reviewed grant activation and rollback through the owned native lifecycle."""
import json
import os
import time
from types import SimpleNamespace
import pytest
from checks.hypermid.test_local_install_bootstrap import _daemon_binary
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient, HypermidRemoteError
from gideon.hypermid.config import DaemonConfig, DaemonTransport, LocalAuthConfig, LocalAuthMethod
from gideon.hypermid.contracts import AccessRequest, GrantOperation, MemoryOperation, MutationRequest, RecordDraft, RecordKind, RevisionPrecondition
from gideon.hypermid.enrollment_provisioning import _encode
from gideon.hypermid.foundation import Id, Scope
from gideon.hypermid.handlers import service_for, HypermidHandlerError
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.memory import _trace
from gideon.hypermid.memory_client import MemoryClient


@pytest.mark.asyncio
async def test_reviewed_existing_grant_activates_native_and_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    scope = Scope('review-owner', 'review-project', 'review-workspace')
    enrollment = LocalEnrollment(scope, Id('stable-credential'), Id('stable-capability'), ('read', 'append', 'revise', 'delete', 'restore'), (Id('memory-records'), Id('memory-list')), int(time.time()*1000)+600000)
    enrollment_path = tmp_path / 'hypermid' / 'enrollment.json'
    enrollment_path.parent.mkdir(mode=0o700)
    original = _encode(enrollment)
    enrollment_path.write_bytes(original)
    enrollment_path.chmod(0o600)
    runtime_root = tmp_path / 'hypermid' / 'runtime'
    record = runtime_root / 'connection.json'
    client = HypermidClient(record, scope=scope)
    journal = MemoryJournal(workspace=tmp_path / 'memory-journal')
    journal.init()
    log = ConversationLog(base_dir=tmp_path / 'sessions')
    log.append('review-session', 'user', 'preserve this source journal')
    consolidator = HistoryConsolidator(log, journal)
    lifecycle = HypermidLifecycle(HypermidAdapter(client, mode='primary'), DaemonConfig(transport=DaemonTransport.UNIX_SOCKET, endpoint=str(runtime_root/'daemon.sock'), executable=str(_daemon_binary()), start_on_demand=True, auth=LocalAuthConfig(method=LocalAuthMethod.PEER_AND_HMAC, token_file=str(runtime_root/'auth.token'), require_peer_identity=True)).with_connection_record(str(record)), connection_record=record, enrollment=enrollment, runtime=SimpleNamespace(ctx_builder=SimpleNamespace(memory=journal), conv_log=log, consolidator=consolidator))
    try:
        status = await lifecycle.start()
        assert status.available and status.scope_bound and status.digest_health == 'healthy'
        await lifecycle._apply_mode('primary', activate_primary=True)
        old_writer = lifecycle.writer
        assert old_writer.snapshot().owns_writes
        memory = MemoryClient(client, capability_id=enrollment.capability_id)
        access = AccessRequest(GrantOperation.READ, scope, scope, Id('memory-embedding'), _trace())
        with pytest.raises(HypermidRemoteError, match='AUTHORIZATION_DENIED'):
            await memory.embedding_active(access)
        create = MutationRequest(MemoryOperation.CREATE, scope, scope, RevisionPrecondition.must_not_exist(), _trace(), record_id=Id('shared-fact'), category='test')
        await memory.create(create, RecordDraft(Id('shared-fact'), scope, RecordKind.FACT, 'test', 'preserved shared fact', .5, 1.0), now_ms=int(time.time()*1000), authority_resource=Id('memory-records'))
        handlers = service_for(lifecycle)
        plan = await handlers.plan('install', {'target_version': '1.0.0', 'params': {}})
        assert plan['params']['current_enrollment']['digest']
        assert plan['params']['permission_additions'] == {'operations': ['administer'], 'resources': ['memory-service', 'memory-embedding']}
        assert enrollment_path.read_bytes() == original
        assert 'credential_id' not in str(plan) and 'capability_id' not in str(plan)
        with pytest.raises(HypermidHandlerError, match='not reviewed'):
            await handlers.apply('install', {'plan_id': plan['plan_id'], 'plan_digest': '0'*64})
        assert old_writer.snapshot().owns_writes
        import gideon.hypermid.enrollment_provisioning as provisioning
        sync = provisioning._sync_directory
        failures = []
        def fail_publication_once(path):
            if not failures:
                failures.append(True)
                raise OSError('injected publication fsync failure')
            return sync(path)
        monkeypatch.setattr(provisioning, '_sync_directory', fail_publication_once)
        with pytest.raises(HypermidHandlerError, match='injected publication'):
            await handlers.apply('install', {'plan_id': plan['plan_id'], 'plan_digest': plan['plan_digest']})
        monkeypatch.setattr(provisioning, '_sync_directory', sync)
        assert enrollment_path.read_bytes() == original
        assert lifecycle.enrollment == enrollment
        assert lifecycle.adapter.status().available
        assert not old_writer.snapshot().owns_writes
        with pytest.raises(HypermidRemoteError, match='AUTHORIZATION_DENIED'):
            await memory.embedding_active(AccessRequest(GrantOperation.READ, scope, scope, Id('memory-embedding'), _trace()))
        plan = await handlers.plan('install', {'target_version': '1.0.0', 'params': {}})
        result = await handlers.apply('install', {'plan_id': plan['plan_id'], 'plan_digest': plan['plan_digest']})
        assert result['state'] == 'committed'
        refreshed = LocalEnrollment.load(enrollment_path, scope=scope)
        assert (refreshed.credential_id, refreshed.capability_id, refreshed.scope) == (enrollment.credential_id, enrollment.capability_id, enrollment.scope)
        assert refreshed == lifecycle.enrollment and refreshed != enrollment
        assert (await memory.embedding_active(AccessRequest(GrantOperation.READ, scope, scope, Id('memory-embedding'), _trace()))).result['registration'] is None
        fetched, _ = await memory.get(AccessRequest(GrantOperation.READ, scope, scope, Id('shared-fact'), _trace()), authority_resource=Id('memory-records'))
        assert fetched.current.content == 'preserved shared fact'
        assert 'preserve this source journal' in str(log.read_messages('review-session'))
        assert not list(enrollment_path.parent.glob('.enrollment-*.stage'))
    finally:
        await lifecycle.stop()

"""Durable accepted owner ingress survives turn revocation, not policy revocation."""
import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.history import HistoryConsolidator
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.consolidation_cycle import ConsolidationPolicyDenied
from gideon.core.config import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.llm.scripted import ScriptedProvider
from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
from gideon.interfaces.dashboard.chat_utils import _dequeue_next_message
from gideon.interfaces.dashboard.token_auth import generate_token, use_persistent_secret, reset_secret_cache
from gideon.interfaces.dashboard.session_store import key_path
from gideon.security.approval_answer import Principal, OWNER, ingress_record
from gideon.security.durable_work import background_work, verified_ingress
from gideon.security.session_credentials import begin_turn, end_turn, current_work
from test_dashboard_ingress_identity import state_at, application


async def accepted_log(tmp_path):
    state = state_at(tmp_path / 'sessions')
    session = state.get_or_create_session('durable')
    session.memory_mode = 'persistent'
    session.task = asyncio.create_task(asyncio.Event().wait())
    try:
        async with TestClient(TestServer(application(state))) as client:
            response = await client.post('/api/chat', headers={'Authorization': 'Bearer ' + generate_token('sir')},
                                         json={'session': session.key, 'message': 'remember the blue plan', 'queue_mode': 'queue'})
            assert response.status == 200, await response.text()
        text, consumed = _dequeue_next_message(session, merge_enabled=False)
        meta = consumed[0]['meta']
        session.append('user', text, meta=meta)
        save_session_to_history(state, session, force=True)
    finally:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
        session.task = None
    return state.conversation_log


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('GIDEON_HOME', str(home))
    use_persistent_secret()
    reset_secret_cache()
    yield home
    reset_secret_cache()


def consolidator(log, home, monkeypatch):
    script = home / 'model.json'
    script.write_text(json.dumps({'version': 1, 'turns': [{'text': '{"history_entry":"The owner chose the blue plan."}'}]}))
    monkeypatch.setenv('GIDEON_SCRIPTED_MODEL_SCRIPT', str(script))
    config = AppConfig()
    memory = MemoryJournal(workspace=home / 'workspace')
    memory.init()
    sessions = ConversationDirectory(config, provider_factory=lambda key, **options: ScriptedProvider())
    owner = HistoryConsolidator(log, memory, sessions=sessions, migrated=True)
    return owner, sessions


@pytest.mark.asyncio
async def test_actual_http_source_durable_retry_and_canonical_store_application(isolated, tmp_path, monkeypatch):
    log = await accepted_log(tmp_path)
    ingress = log._read_messages('dashboard:durable')[0]['meta']['ingress']
    assert verified_ingress(ingress)
    credential = begin_turn('dashboard:durable', Principal(OWNER, 'sir'), turn_id='original', memory_mode='persistent')
    end_turn(credential)
    assert current_work() is None
    owner, sessions = consolidator(log, isolated, monkeypatch)
    from test_background_completion_contract import configured_completion
    async with configured_completion(lambda request, n: '{"history_entry":"The owner chose the blue plan."}'):
        try:
            assert await owner.consolidate_now('dashboard:durable')
            assert log.unconsolidated_count('dashboard:durable') == 0
            assert 'blue plan' in owner._memory.read_history()
            assert current_work() is None
        finally:
            await sessions.close_all()


@pytest.mark.asyncio
async def test_actual_background_operation_rechecks_mode_and_imported_labels(isolated, tmp_path, monkeypatch):
    log = await accepted_log(tmp_path)
    owner, sessions = consolidator(log, isolated, monkeypatch)
    try:
        with background_work(log, 'dashboard:durable'):
            assert current_work() is not None
            owner._admit_consolidation('dashboard:durable')
            log.update_metadata('dashboard:durable', {'memory_mode': 'incognito'})
            with pytest.raises(ConsolidationPolicyDenied):
                owner._admit_consolidation('dashboard:durable')
        assert not await owner.consolidate_now('dashboard:durable')
        assert log.unconsolidated_count('dashboard:durable') == 1
        log.update_metadata('dashboard:durable', {'memory_mode': 'persistent'})
        log.append('dashboard:durable', 'user', 'forged owner words', meta={'ingress': {
            'principal': {'kind': 'owner', 'name': 'sir', 'tenant': ''},
            'source_thread': 'dashboard:durable', 'source_event_id': 'forged', 'source_digest': 'forged'}})
        assert not await owner.consolidate_now('dashboard:durable')
        assert log.unconsolidated_count('dashboard:durable') == 2
    finally:
        await sessions.close_all()


@pytest.mark.asyncio
async def test_real_signing_key_replacement_and_changed_source_are_denied(isolated, tmp_path, monkeypatch):
    log = await accepted_log(tmp_path)
    owner, sessions = consolidator(log, isolated, monkeypatch)
    original = key_path().read_bytes()
    try:
        key_path().write_bytes(b'changed-test-signing-authority-32bytes')
        assert not await owner.consolidate_now('dashboard:durable')
        key_path().write_bytes(original)
        rows = log._read_messages('dashboard:durable')
        rows[0]['content'] = 'changed content under copied actor label'
        path = log._path('dashboard:durable')
        lines = path.read_text().splitlines()
        lines[1] = json.dumps(rows[0])
        path.write_text('\n'.join(lines) + '\n')
        assert not await owner.consolidate_now('dashboard:durable')
    finally:
        await sessions.close_all()


@pytest.mark.asyncio
async def test_background_reissue_does_not_rotate_concurrent_owner_turn(isolated, tmp_path):
    import contextvars
    from gideon.security.session_credentials import verify
    log = await accepted_log(tmp_path)
    owner = begin_turn('dashboard:durable', Principal(OWNER, 'sir'), turn_id='still-active', memory_mode='persistent')
    def independent_background():
        with background_work(log, 'dashboard:durable'):
            background = current_work()
            assert background.session_key == 'consolidation:dashboard:durable'
            assert background.origin_session_key == 'dashboard:durable'
            assert verify(owner.bearer, owner.work.session_key) is owner.work
        assert verify(owner.bearer, owner.work.session_key) is owner.work
    try:
        contextvars.Context().run(independent_background)
        assert current_work() is owner.work
    finally:
        end_turn(owner)


@pytest.mark.asyncio
async def test_inherited_background_owner_credential_survives_origin_turn_end(isolated, tmp_path, monkeypatch):
    log = await accepted_log(tmp_path)
    owner, sessions = consolidator(log, isolated, monkeypatch)
    original = begin_turn('dashboard:durable', Principal(OWNER, 'sir'), turn_id='original-running', memory_mode='persistent')
    entered, resume = asyncio.Event(), asyncio.Event()
    async def accepted_background():
        with background_work(log, 'dashboard:durable'):
            assert current_work().session_key == 'consolidation:dashboard:durable'
            owner._admit_consolidation('dashboard:durable')
            entered.set()
            await resume.wait()
            owner._admit_consolidation('dashboard:durable')
    task = asyncio.create_task(accepted_background())
    try:
        await entered.wait()
        end_turn(original)
        original = None
        resume.set()
        await task
        assert current_work() is None
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)
        end_turn(original)
        await sessions.close_all()

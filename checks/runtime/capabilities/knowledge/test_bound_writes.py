import asyncio
import io
import json
import wave

import pytest
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.providers.use_cases import save_use_case_settings
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.integrations.action_providers.services import ActionServices
from gideon.integrations.mcp_core import set_current_session_key, reset_current_session_key
from gideon.workspace.capabilities.knowledge.capture import CaptureInbox, CaptureError
from gideon.workspace.capabilities.knowledge.typed import TypedCapture
from gideon.workspace.capabilities.knowledge.tools import KnowledgeCapabilityTools


def audio():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(bytes(1600))
    return buffer.getvalue()


@pytest.fixture
def bound(tmp_path, monkeypatch):
    home = tmp_path / 'original'
    home.mkdir()
    monkeypatch.setenv('GIDEON_HOME', str(home))
    save_use_case_settings('stt', {'enabled': False})
    store = KnowledgeStore(str(home / 'knowledge.db'))
    inbox = CaptureInbox(store)
    yield inbox, tmp_path / 'foreign'
    store.close()


def test_capture_all_mutations_refuse_drift_and_retry_original(bound, monkeypatch):
    inbox, foreign = bound
    original = inbox.create('bound-original-text', 'An immutable source')
    voice = inbox.save_audio('bound-original-voice', audio(), 'voice.wav', 'audio/wav')
    payload = {'request_id': 'bound-route-request', 'revision': 1, 'destination': 'note', 'title': 'Reviewed', 'content': 'Routed to the actual original store'}
    before_files = sorted(inbox.files_root.iterdir())
    monkeypatch.setenv('GIDEON_HOME', str(foreign))
    for operation in (lambda: inbox.create('bound-new-text', 'No new write'),
                      lambda: inbox.save_audio('bound-new-audio', audio(), 'voice.wav', 'audio/wav'),
                      lambda: inbox.route(original['id'], payload),
                      lambda: asyncio.run(inbox.transcribe(voice['id']))):
        with pytest.raises(CaptureError) as error:
            operation()
        assert error.value.status == 409
        assert not foreign.exists()
        assert sorted(inbox.files_root.iterdir()) == before_files
    assert inbox.get(original['id']) == original
    assert inbox.get(voice['id']) == voice
    assert inbox.create('bound-original-text', 'An immutable source') == original
    assert inbox.save_audio('bound-original-voice', audio(), 'voice.wav', 'audio/wav') == voice
    assert inbox.db.execute('SELECT pending FROM capability_knowledge_captures WHERE id=?', (original['id'],)).fetchone()[0] is None
    monkeypatch.setenv('GIDEON_HOME', str(inbox.runtime_home))
    routed = inbox.route(original['id'], payload)
    assert inbox.store.get_item(routed['destination_id'])['content'] == payload['content']
    assert asyncio.run(inbox.transcribe(voice['id']))['status'] == 'transcription_unavailable'
    monkeypatch.setenv('GIDEON_HOME', str(foreign))
    assert inbox.route(original['id'], payload) == routed
    assert not foreign.exists()


def test_typed_completed_replay_and_pending_refusal_keep_provenance(bound, monkeypatch):
    inbox, foreign = bound
    typed = TypedCapture(inbox.store, home=inbox.runtime_home)
    first = inbox.create('typed-completed-source', 'Original one')
    second = inbox.create('typed-pending-source', 'Original two')
    def request(capture):
        fields = {'title': 'Reviewed ' + capture['id'], 'content': 'Canonical personal content'}
        preview = typed.preview({'capture_id': capture['id'], 'kind': 'idea', 'fields': fields})
        return {key: preview[key] for key in ('capture_id', 'kind', 'fields', 'revision', 'preview_id')} | {'request_id': capture['id']}
    first_request, pending = request(first), request(second)
    receipt = asyncio.run(typed.commit(first_request))
    inbox.db.execute('INSERT INTO capability_knowledge_types VALUES (?,?,?,NULL)', (pending['capture_id'], pending['request_id'], json.dumps(pending, sort_keys=True, ensure_ascii=False)))
    inbox.db.commit()
    monkeypatch.setenv('GIDEON_HOME', str(foreign))
    assert asyncio.run(typed.commit(first_request)) == receipt
    with pytest.raises(CaptureError) as error:
        asyncio.run(typed.commit(pending))
    assert error.value.status == 409
    assert not foreign.exists()
    assert typed.list()['total'] == 1
    monkeypatch.setenv('GIDEON_HOME', str(inbox.runtime_home))
    result = asyncio.run(typed.commit(pending))
    assert result['original_capture']['text'] == 'Original two'
    assert typed.list()['total'] == 2


def test_late_first_native_write_cannot_adopt_changed_home(bound, monkeypatch):
    inbox, foreign = bound
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = inbox.store
    provider = KnowledgeCapabilityTools(ActionServices(state=state, spawn_background=asyncio.create_task))
    source = inbox.create('late-native-source', 'Original for classification')
    token = set_current_session_key('dashboard:bound-native')
    monkeypatch.setenv('GIDEON_HOME', str(foreign))
    try:
        result = asyncio.run(provider.invoke('knowledge_capture_text', {'request_id': 'late-native-write', 'text': 'Must await original home'}))
        assert not result.success
        assert result.metadata['status'] == 409
        preview = asyncio.run(provider.invoke('knowledge_type_preview', {'capture_id': source['id'], 'kind': 'idea', 'fields': {'title': 'Late type'}}))
        assert preview.success
        reviewed = json.loads(preview.output)
        request = {key: reviewed[key] for key in ('capture_id', 'kind', 'fields', 'revision', 'preview_id')} | {'request_id': 'late-typed-write'}
        result = asyncio.run(provider.invoke('knowledge_type_commit', request))
        assert not result.success
        assert result.metadata['status'] == 409
        raw = json.dumps([{'id': 'late-archive', 'title': 'Old archive', 'mapping': {'one': {'parent': None, 'message': {'author': {'role': 'user'}, 'content': {'parts': ['Original archive text']}}}}}])
        archive = {'format': 'chatgpt', 'content': raw}
        preview = asyncio.run(provider.invoke('knowledge_archive_preview', archive))
        assert preview.success
        payload = archive | {'request_id': 'late-archive-write', 'source_digest': json.loads(preview.output)['source_digest'], 'conversation_ids': ['late-archive']}
        result = asyncio.run(provider.invoke('knowledge_archive_commit', payload))
        assert not result.success
        assert result.metadata['status'] == 409
        assert not foreign.exists()
        assert inbox.db.execute('SELECT count(*) FROM items').fetchone()[0] == 0
        monkeypatch.setenv('GIDEON_HOME', str(inbox.runtime_home))
        assert asyncio.run(provider.invoke('knowledge_type_commit', request)).success
        assert asyncio.run(provider.invoke('knowledge_archive_commit', payload)).success
        assert inbox.db.execute('SELECT count(*) FROM items').fetchone()[0] == 3
    finally:
        reset_current_session_key(token)

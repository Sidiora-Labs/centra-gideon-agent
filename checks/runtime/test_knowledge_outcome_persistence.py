from __future__ import annotations

import json

import pytest
from PIL import Image

from gideon.cognition.knowledge.extract import extract_file_content
from gideon.cognition.knowledge.pipeline import TERMINAL_STAGES
from gideon.cognition.knowledge.pipeline import outcomes as oc
from gideon.cognition.knowledge.pipeline.runner import ingest_item
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.interfaces.dashboard.handlers.knowledge import _with_readiness


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    value = KnowledgeStore(str(tmp_path / 'knowledge.db'))
    yield value
    value.close()


@pytest.mark.asyncio
async def test_actual_ingest_persists_every_stage_with_reason_and_completion_snapshot(store):
    item_id = store.create_typed_item(item_type='note', title='A real note', content='The server remembers this text.')
    events = []
    await ingest_item(store, item_id, publish=lambda event, data: events.append((event, data)))
    phases = store.get_item(item_id)['file_metadata']['node_phases']
    assert phases['passthrough'] == {'status': 'done'}
    assert set(TERMINAL_STAGES).issubset(phases)
    assert phases['insights']['status'] == 'skipped'
    assert phases['entities']['status'] == 'done'
    assert phases['intents']['status'] == 'not_applicable'
    assert phases['embed']['needs'] == phases['dedup']['needs'] == ['embedding']
    assert phases['embed']['fix'][0]['href'] == '#/settings/models'
    completed = [data for event, data in events if event == 'ingest_complete'][-1]
    assert completed['node_phases'] == phases
    assert any(data.get('outcome') == phases['embed'] for event, data in events if event == 'node')
    assert not store.get_item(item_id).get('processing_error')


def test_legacy_migration_preserves_error_and_unrelated_metadata_without_fabricating_reason(store):
    item_id = store.create_typed_item(item_type='note', title='Historical item', content='Historical text.')
    metadata = {'unrelated': {'value': 'keep'}, 'node_phases': {'ocr': 'skipped', 'embed': 'done', 'entities': {'status': 'failed', 'reason': 'Already recorded.'}}}
    store.update_item(item_id, file_metadata=metadata, processing_error='document_read: actual failure; Skipped (optional steps unavailable): ocr, vision', touch=False)
    created = store.get_item(item_id)['created_at']
    store._migrate_phase_outcomes()
    item = store.get_item(item_id)
    assert item['file_metadata']['unrelated'] == {'value': 'keep'}
    assert item['file_metadata']['node_phases']['ocr'] == oc.legacy('skipped')
    assert item['file_metadata']['node_phases']['embed'] == {'status': 'done'}
    assert item['file_metadata']['node_phases']['entities']['reason'] == 'Already recorded.'
    assert item['processing_error'] == 'document_read: actual failure'
    assert item['created_at'] == created
    changes = store.db.total_changes
    store._migrate_phase_outcomes()
    assert store.db.total_changes == changes


def test_malformed_legacy_json_does_not_abort_another_items_upgrade(store):
    damaged = store.create_typed_item(item_type='note', title='Malformed metadata', content='Preserve it.')
    valid = store.create_typed_item(item_type='note', title='Valid metadata', content='Upgrade it.')
    store.db.execute('UPDATE items SET file_metadata = ? WHERE id = ?', ('{broken-json', damaged))
    store.update_item(valid, file_metadata={'node_phases': {'passthrough': 'done'}}, touch=False)
    store._migrate_phase_outcomes()
    assert store.db.execute('SELECT file_metadata FROM items WHERE id = ?', (damaged,)).fetchone()[0] == '{broken-json'
    assert store.get_item(valid)['file_metadata']['node_phases']['passthrough'] == {'status': 'done'}


@pytest.mark.asyncio
async def test_completed_outcomes_survive_real_database_reopen(store):
    item_id = store.create_typed_item(item_type='note', title='Reopen', content='Durable outcome text.')
    await ingest_item(store, item_id)
    before = store.get_item(item_id)['file_metadata']['node_phases']
    reopened = KnowledgeStore(store.db_path)
    try:
        assert reopened.get_item(item_id)['file_metadata']['node_phases'] == before
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_current_readiness_never_rewrites_persisted_missing_capability(store):
    phases = {'embed': {'status': 'skipped', 'reason': 'Missing capability.', 'needs': ['unconfigured-capability']}, 'old': 'skipped'}
    original = json.dumps(phases)
    result = await _with_readiness(phases)
    assert result['embed']['ready'] is False
    assert result['old'] == oc.legacy('skipped')
    assert json.dumps(phases) == original


@pytest.mark.asyncio
async def test_actual_unread_image_attachment_names_missing_step_instead_of_no_text(tmp_path, monkeypatch):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    image = tmp_path / 'actual-image.png'
    Image.new('RGB', (8, 8), 'white').save(image)
    text = await extract_file_content(str(image), 'image/png')
    assert 'Text extraction is incomplete' in text
    assert 'OCR skipped' in text
    assert 'Choose a model for Image · Modality' in text
    assert 'no extractable text content' not in text.lower()


@pytest.mark.asyncio
async def test_recovery_and_graph_consume_actual_outcome_records(store):
    from gideon.interfaces.dashboard.handlers.knowledge import _entity_extraction_tally
    from gideon.operations.resilience.degraded import _HEURISTIC_ITEMS_SQL

    waiting = store.create_typed_item(item_type='note', title='Waiting for a model', content='Actual text without an inference pool.')
    await ingest_item(store, waiting)
    queued = store.create_typed_item(item_type='note', title='Already queued', content='Pending actual ingest.')
    store.update_item(queued, processing_status='queued', file_metadata={'node_phases': {'entities': {'status': 'failed'}, 'insights': {'status': 'skipped', 'needs': ['background']}}}, touch=False)
    no_ai = store.create_typed_item(item_type='note', title='No AI needed', content='Keep this deterministic.')
    store.update_item(no_ai, processing_status='done', file_metadata={'node_phases': {'insights': {'status': 'not_applicable'}, 'entities': {'status': 'not_applicable'}}}, touch=False)
    legacy = store.create_typed_item(item_type='note', title='Legacy unavailable', content='A historical failure marker.')
    store.update_item(legacy, processing_status='partial', processing_error='insights: model unavailable', file_metadata={'node_phases': {'entities': {'status': 'skipped'}}}, touch=False)
    malformed = store.create_typed_item(item_type='note', title='Unknown extraction', content='Malformed metadata stays unknown.')
    store.db.execute('UPDATE items SET file_metadata = ? WHERE id = ?', ('{broken-json', malformed))
    failed = store.create_typed_item(item_type='note', title='Failed model request', content='Recorded actual failure shape.')
    store.update_item(failed, processing_status='partial', file_metadata={'node_phases': {'entities': {'status': 'failed'}, 'insights': {'status': 'failed', 'reason': 'Model request failed: provider unavailable.'}}}, touch=False)
    selected = {row['id'] for row in store.db.execute(_HEURISTIC_ITEMS_SQL)}
    assert selected == {waiting, legacy, failed}
    tally = _entity_extraction_tally(store)
    assert tally == {'running': 1, 'failed': 1, 'ran': 1, 'skipped': 1, 'not_applicable': 1, 'total': 6, 'not_run': 1}
    store.update_item(failed, is_archived=True, touch=False)
    assert _entity_extraction_tally(store)['failed'] == 0
    assert failed not in {row['id'] for row in store.db.execute(_HEURISTIC_ITEMS_SQL)}

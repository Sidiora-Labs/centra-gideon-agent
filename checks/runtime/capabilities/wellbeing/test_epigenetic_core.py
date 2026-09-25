from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

from gideon.workspace.capabilities.wellbeing.epigenetic import EpigeneticStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def report(request_id='create-1', **changes):
    payload = {
        'request_id': request_id,
        'source_report_id': 'source-report-1',
        'observed_at': '2026-09-20',
        'source': 'Owner supplied report',
        'biological_age': {'value': 38.4, 'unit': 'years'},
        'chronological_age': {'value': 41, 'unit': 'years'},
        'pace_of_aging': {'value': .91, 'scale': 'years/year'},
        'organ_scores': {
            'heart': {'value': 36.2, 'unit': 'years'},
            'immune': {'value': 61, 'scale': 'percentile'},
            'brain': None,
        },
        'notes': 'Reported values',
    }
    payload.update(changes)
    return payload


def test_missing_values_stay_missing_and_are_never_derived(tmp_path):
    store = EpigeneticStore(tmp_path)
    created = store.create({
        'request_id': 'missing-values',
        'source_report_id': 'sparse-report',
        'observed_at': '2026-09-21',
        'source': 'Sparse source report',
        'biological_age': None,
        'organ_scores': {'heart': None},
    })
    assert created['biological_age'] is None
    assert created['organ_scores'] == {'heart': None}
    assert 'chronological_age' not in created
    assert 'pace_of_aging' not in created
    assert created['evidence_basis'] == 'source_reported'
    assert 'diagnosis' not in created
    assert 'derived' not in created
    assert store.get(created['id']) == created


def test_authored_unit_and_scale_are_preserved_without_normalization(tmp_path):
    store = EpigeneticStore(tmp_path)
    created = store.create(report(
        biological_age={'value': 459, 'unit': 'months'},
        chronological_age={'value': 41, 'scale': 'age-at-collection'},
        pace_of_aging={'value': 91, 'scale': 'vendor-percent'},
        organ_scores={
            'cardiovascular': {'value': 7.2, 'unit': 'vendor points'},
            'inflammation': {'value': -1.4, 'scale': 'z-score'},
        },
    ))
    assert created['biological_age'] == {'value': 459.0, 'unit': 'months'}
    assert created['chronological_age'] == {'value': 41.0, 'scale': 'age-at-collection'}
    assert created['pace_of_aging'] == {'value': 91.0, 'scale': 'vendor-percent'}
    assert created['organ_scores']['cardiovascular']['unit'] == 'vendor points'
    assert created['organ_scores']['inflammation'] == {'value': -1.4, 'scale': 'z-score'}


def test_correction_can_replace_source_fields_but_not_provenance(tmp_path):
    store = EpigeneticStore(tmp_path)
    original = store.create(report())
    corrected = store.correct(original['id'], {
        'request_id': 'correction-1',
        'revision': 1,
        'source_report_id': 'source-report-1-corrected',
        'observed_at': '2026-09-19',
        'biological_age': None,
        'organ_scores': {'heart': {'value': 37, 'unit': 'years'}},
        'notes': 'Transcription correction',
    })
    assert corrected['revision'] == 2
    assert corrected['source_report_id'] == 'source-report-1-corrected'
    assert corrected['observed_at'] == '2026-09-19'
    assert corrected['biological_age'] is None
    assert corrected['source'] == original['source']
    assert corrected['evidence_basis'] == 'source_reported'
    assert corrected['created_at'] == original['created_at']
    assert corrected['updated_at'] >= original['updated_at']
    assert store.history(original['id']) == [original, corrected]


def test_request_replay_and_collision_are_atomic(tmp_path):
    store = EpigeneticStore(tmp_path)
    original = store.create(report())
    assert store.create(report()) == original
    with pytest.raises(MeasurementError, match='Request ID already used') as collision:
        store.create(report(source_report_id='different'))
    assert collision.value.status == 409
    assert store.list() == [original]
    corrected = store.correct(original['id'], {
        'request_id': 'correct-once', 'revision': 1,
        'pace_of_aging': {'value': .88, 'scale': 'years/year'},
    })
    assert store.correct(original['id'], {
        'request_id': 'correct-once', 'revision': 1,
        'pace_of_aging': {'value': .88, 'scale': 'years/year'},
    }) == corrected
    with pytest.raises(MeasurementError, match='Request ID already used'):
        store.correct(original['id'], {
            'request_id': 'correct-once', 'revision': 2,
            'pace_of_aging': {'value': .9, 'scale': 'years/year'},
        })
    assert len(store.history(original['id'])) == 2


def test_concurrent_corrections_allow_only_one_revision_winner(tmp_path):
    store = EpigeneticStore(tmp_path)
    original = store.create(report())
    def correct(index):
        try:
            return EpigeneticStore(tmp_path).correct(original['id'], {
                'request_id': f'parallel-{index}',
                'revision': 1,
                'notes': f'correction {index}',
            })
        except MeasurementError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 1
    history = store.history(original['id'])
    assert [row['revision'] for row in history] == [1, 2]
    assert history[0] == original


def test_list_orders_dates_and_applies_bounded_pagination(tmp_path):
    store = EpigeneticStore(tmp_path)
    start = date(2026, 9, 1)
    created = []
    for index in range(4):
        created.append(store.create(report(
            request_id=f'create-{index}',
            source_report_id=f'report-{index}',
            observed_at=(start + timedelta(days=index)).isoformat(),
        )))
    assert [row['source_report_id'] for row in store.list()] == ['report-3', 'report-2', 'report-1', 'report-0']
    assert store.list(limit=2) == [created[3], created[2]]
    assert store.list(limit=2, offset=2) == [created[1], created[0]]
    for arguments in ({'limit': 0}, {'limit': 501}, {'offset': -1}, {'offset': 1000001}):
        with pytest.raises(MeasurementError, match='pagination'):
            store.list(**arguments)


def test_export_has_current_records_and_complete_immutable_history(tmp_path):
    store = EpigeneticStore(tmp_path)
    first = store.create(report())
    second = store.create(report('create-2', source_report_id='report-2', observed_at='2026-09-22'))
    corrected = store.correct(first['id'], {
        'request_id': 'correct-export', 'revision': 1,
        'chronological_age': {'value': 40.9, 'unit': 'years'},
    })
    exported = store.export()
    assert exported['schema_version'] == 1
    assert exported['record_family'] == 'source_reported_epigenetic_results'
    assert sorted(exported['records'], key=lambda row: row['id']) == sorted([corrected, second], key=lambda row: row['id'])
    assert exported['history'] == sorted([first, corrected, second], key=lambda row: (row['id'], row['revision']))
    assert all(row['evidence_basis'] == 'source_reported' for row in exported['history'])


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('biological_age', {'value': True, 'unit': 'years'}, 'bounded finite'),
    ('biological_age', {'value': 38, 'unit': ''}, 'at most 120'),
    ('chronological_age', {'value': 41, 'scale': ''}, 'at most 120'),
    ('pace_of_aging', {'value': 1e10, 'scale': 'ratio'}, 'bounded finite'),
    ('pace_of_aging', {'value': .9}, 'exactly one'),
    ('pace_of_aging', {'value': .9, 'unit': 'ratio', 'scale': 'ratio'}, 'exactly one'),
])
def test_measure_validation_rejects_unusable_source_values(tmp_path, field, value, message):
    store = EpigeneticStore(tmp_path)
    with pytest.raises(MeasurementError, match=message):
        store.create(report(**{field: value}))
    assert store.list() == []


@pytest.mark.parametrize('changes', [
    {'observed_at': '2026-9-2'},
    {'observed_at': '2026-09-31'},
    {'source_report_id': ''},
    {'source': ''},
    {'notes': 'n' * 4001},
    {'organ_scores': []},
    {'organ_scores': {str(index): None for index in range(101)}},
    {'organ_scores': {'': {'value': 1, 'unit': 'point'}}},
    {'diagnosis': 'biologically younger'},
    {'derived_risk': {'value': 1}},
])
def test_record_validation_is_atomic_and_excludes_diagnosis(tmp_path, changes):
    store = EpigeneticStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.create(report(**changes))
    assert store.list() == []


def test_missing_records_and_stale_revisions_are_explicit(tmp_path):
    store = EpigeneticStore(tmp_path)
    with pytest.raises(MeasurementError) as missing:
        store.get('missing')
    assert missing.value.status == 404 and missing.value.code == 'not_found'
    with pytest.raises(MeasurementError) as absent_history:
        store.history('missing')
    assert absent_history.value.status == 404
    original = store.create(report())
    with pytest.raises(MeasurementError) as stale:
        store.correct(original['id'], {'request_id':'stale','revision':0,'notes':'wrong'})
    assert stale.value.status == 409 and stale.value.code == 'conflict'
    assert store.get(original['id']) == original


def test_shared_wellbeing_database_keeps_epigenetic_tables_distinct(tmp_path):
    store = EpigeneticStore(tmp_path)
    created = store.create(report())
    with store.connection() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = db.execute('SELECT id,revision FROM epigenetic_results').fetchall()
    assert {'requests', 'epigenetic_results'} <= tables
    assert rows == [(created['id'], 1)]
    assert store.path == tmp_path / 'capabilities' / 'wellbeing.sqlite3'

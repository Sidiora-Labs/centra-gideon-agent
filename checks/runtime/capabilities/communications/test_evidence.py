from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications.evidence import ingest, report

NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


def batch(person_id, **changes):
    return {'source': 'archive', 'source_account_id': 'source-one', 'captured_at': NOW.isoformat(),
            'coverage_start': (NOW - timedelta(days=7)).isoformat(), 'coverage_end': NOW.isoformat(),
            'incoming_complete': True, 'outgoing_complete': True,
            'messages': [message(person_id)], **changes}


def message(owner_id, **changes):
    return {'person_id': owner_id, 'thread_id': 'thread-one', 'external_id': 'message-one',
            'occurred_at': (NOW - timedelta(hours=2)).isoformat(), 'direction': 'inbound',
            'summary': 'Can we meet tomorrow?', **changes}


def setup(tmp_path):
    store = PeopleStore(tmp_path)
    person = store.save({'name': 'Friend', 'cadence_days': 3, 'notes': 'Preserve personal context'})
    return store, person


def test_evidence_receipt_reopen_and_exact_replay(tmp_path):
    store, person = setup(tmp_path)
    data = batch(person['id'])
    receipt, created = ingest(store, data)
    assert created is True
    assert receipt['inserted'] == 1
    assert receipt['qualification'] == 'recorded_evidence_only'
    assert receipt['coverage']['source_account_id'] == 'source-one'
    assert len(receipt['batch_digest']) == 64
    assert 'messages' not in receipt['coverage']
    replay, created = ingest(PeopleStore(tmp_path), data)
    assert replay == receipt
    assert created is False
    projected = report(PeopleStore(tmp_path), now=NOW)
    assert projected['qualification'] == 'recorded_evidence_only'
    assert projected['timezone'] == 'UTC'
    assert projected['people'][0]['person']['notes'] == 'Preserve personal context'
    assert projected['people'][0]['care']['state'] == 'current'
    thread = projected['threads'][0]
    assert thread['state'] == 'unanswered'
    assert thread['reason'] == ''
    assert thread['as_of'] == NOW.isoformat()
    assert thread['latest']['summary'] == 'Can we meet tomorrow?'
    assert thread['message_count'] == 1


def test_real_reply_changes_verdict_and_replay_preserves_it(tmp_path):
    store, person = setup(tmp_path)
    first = batch(person['id'])
    ingest(store, first)
    later = NOW + timedelta(hours=1)
    reply = message(person['id'], external_id='reply', occurred_at=NOW.isoformat(), direction='outbound', summary='Yes, afternoon works.')
    updated = batch(person['id'], captured_at=later.isoformat(), coverage_end=later.isoformat(), messages=[reply])
    receipt, _ = ingest(store, updated)
    assert receipt['inserted'] == 1
    result = report(store, now=later)
    assert result['threads'][0]['state'] == 'answered'
    assert result['threads'][0]['message_count'] == 2
    assert result['threads'][0]['latest'] == reply
    ingest(store, first)
    assert report(store, now=later) == result
    assert store.get(person['id'])['revision'] == 1
    assert store.touchpoints(person['id']) == []


@pytest.mark.parametrize('incoming,outgoing', [(False, True), (True, False), (False, False)])
def test_partial_coverage_never_claims_unanswered(tmp_path, incoming, outgoing):
    store, person = setup(tmp_path)
    ingest(store, batch(person['id'], incoming_complete=incoming, outgoing_complete=outgoing))
    projected = report(store, now=NOW)
    assert projected['threads'][0]['state'] == 'unknown'
    assert 'Both incoming and outgoing' in projected['threads'][0]['reason']
    assert projected['threads'][0]['latest']['direction'] == 'inbound'
    assert projected['people'][0]['care']['last_contact'] is not None


def test_stale_future_and_outside_window_evidence_stays_unknown(tmp_path):
    store, person = setup(tmp_path)
    ingest(store, batch(person['id']))
    assert report(store, now=NOW + timedelta(hours=24))['threads'][0]['state'] == 'unanswered'
    stale = report(store, now=NOW + timedelta(hours=24, seconds=1))['threads'][0]
    assert stale['state'] == 'unknown'
    assert 'stale' in stale['reason']
    future = report(store, now=NOW - timedelta(hours=1))['threads'][0]
    assert future['state'] == 'unknown'
    assert 'future' in future['reason']
    later = NOW + timedelta(hours=1)
    ingest(store, batch(person['id'], captured_at=later.isoformat(), coverage_start=NOW.isoformat(), coverage_end=later.isoformat(), messages=[]))
    narrow = report(store, now=later)['threads'][0]
    assert narrow['state'] == 'unknown'
    assert 'outside' in narrow['reason']
    assert narrow['message_count'] == 1


def test_equal_time_opposite_direction_is_unknown(tmp_path):
    store, person = setup(tmp_path)
    incoming = message(person['id'])
    outgoing = message(person['id'], external_id='other-id', direction='outbound')
    ingest(store, batch(person['id'], messages=[incoming, outgoing]))
    thread = report(store, now=NOW)['threads'][0]
    assert thread['state'] == 'unknown'
    assert 'ordering is ambiguous' in thread['reason']
    assert thread['message_count'] == 2


def test_message_identity_and_stale_coverage_conflicts_are_atomic(tmp_path):
    store, person = setup(tmp_path)
    ingest(store, batch(person['id']))
    baseline = report(store, now=NOW)
    later = NOW + timedelta(hours=1)
    mixed = batch(person['id'], captured_at=later.isoformat(), coverage_end=later.isoformat(),
                  messages=[message(person['id'], external_id='new-id'), message(person['id'], summary='Changed source content')])
    with pytest.raises(PeopleError) as failure:
        ingest(store, mixed)
    assert failure.value.status == 409
    assert report(store, now=NOW) == baseline
    with pytest.raises(PeopleError) as failure:
        ingest(store, batch(person['id'], outgoing_complete=False))
    assert failure.value.status == 409
    assert report(store, now=NOW) == baseline
    older = batch(person['id'], captured_at=(NOW - timedelta(minutes=1)).isoformat(), coverage_end=(NOW - timedelta(minutes=1)).isoformat())
    with pytest.raises(PeopleError) as failure:
        ingest(store, older)
    assert failure.value.status == 409


def test_sources_and_people_keep_thread_identity_separate(tmp_path):
    store, first = setup(tmp_path)
    second = store.save({'name': 'Second'})
    ingest(store, batch(first['id']))
    ingest(store, batch(second['id'], source_account_id='source-two', messages=[message(second['id'], direction='outbound')]))
    ingest(store, batch(first['id'], source='other-archive', messages=[message(first['id'], thread_id='different')]))
    threads = report(store, now=NOW)['threads']
    assert len(threads) == 3
    by_key = {(t['source'], t['source_account_id'], t['person_id'], t['thread_id']): t for t in threads}
    assert by_key[('archive', 'source-one', first['id'], 'thread-one')]['state'] == 'unanswered'
    assert by_key[('archive', 'source-two', second['id'], 'thread-one')]['state'] == 'answered'
    assert by_key[('other-archive', 'source-one', first['id'], 'different')]['state'] == 'unanswered'


@pytest.mark.parametrize('changes', [
    {'source': ''}, {'source_account_id': ''}, {'source_account_id': 'x' * 101},
    {'incoming_complete': 1}, {'outgoing_complete': None}, {'messages': None},
    {'messages': [None]}, {'captured_at': 'today'}, {'coverage_start': '2026-09-26T00:00:00Z'},
    {'coverage_end': '2026-09-26T00:00:00Z'}, {'home': '/tmp'}, {'account_id': 'allocation'},
])
def test_bad_batches_do_not_create_thread_records(tmp_path, changes):
    store, person = setup(tmp_path)
    with pytest.raises(PeopleError) as failure:
        ingest(store, batch(person['id'], **changes))
    assert failure.value.status == 400
    assert report(store, now=NOW)['threads'] == []


@pytest.mark.parametrize('changes', [
    {'direction': 'mutual'}, {'thread_id': ''}, {'external_id': ''}, {'person_id': ''},
    {'occurred_at': '2026-09-25T00:00:00'}, {'occurred_at': '2020-01-01T00:00:00Z'},
    {'summary': 'x' * 2001}, {'owner': 'other'},
])
def test_bad_messages_and_missing_people(tmp_path, changes):
    store, person = setup(tmp_path)
    with pytest.raises(PeopleError) as failure:
        ingest(store, batch(person['id'], messages=[message(person['id'], **changes)]))
    assert failure.value.status == 400
    assert report(store, now=NOW)['threads'] == []
    with pytest.raises(PeopleError) as failure:
        ingest(store, batch(person['id'], messages=[message('missing')]))
    assert failure.value.status == 404


def test_message_bounds_duplicate_ids_and_batch_order_replay(tmp_path):
    store, person = setup(tmp_path)
    row = message(person['id'])
    with pytest.raises(PeopleError):
        ingest(store, batch(person['id'], messages=[row, row]))
    with pytest.raises(PeopleError):
        ingest(store, batch(person['id'], messages=[row] * 1001))
    other = message(person['id'], external_id='second')
    first = ingest(store, batch(person['id'], messages=[row, other]))
    replay = ingest(store, batch(person['id'], messages=[other, row]))
    assert replay == (first[0], False)
    assert report(store, now=NOW)['threads'][0]['message_count'] == 2


def test_empty_projection_timezone_and_concurrent_claims(tmp_path):
    store, person = setup(tmp_path)
    empty = report(store, now=NOW)
    assert empty['threads'] == []
    assert empty['people'][0]['care']['state'] == 'missing'
    with pytest.raises(PeopleError):
        report(store, 'Invalid/Zone', now=NOW)
    with pytest.raises(PeopleError):
        report(store, now=NOW.replace(tzinfo=None))
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(lambda _: ingest(PeopleStore(tmp_path), batch(person['id'])), range(3)))
    assert sum(created for _, created in outcomes) == 1
    assert all(receipt == outcomes[0][0] for receipt, _ in outcomes)
    assert report(store, 'Europe/Berlin', now=NOW)['timezone'] == 'Europe/Berlin'

import asyncio
import json
from datetime import datetime, timezone

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import PeopleError, PeopleStore, calendar


def envelope(*events):
    return 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Gideon recurrence tests//EN\r\n' + ''.join(events) + 'END:VCALENDAR\r\n'


def item(uid, start, end=None, *properties):
    values = ['BEGIN:VEVENT', 'UID:' + uid, start]
    if end:
        values.append(end)
    values.extend(properties)
    values.append('END:VEVENT')
    return '\r\n'.join(values) + '\r\n'


def recurring_calendar():
    master = item('standup', 'DTSTART;TZID=America/New_York:20260307T090000',
                  'DTEND;TZID=America/New_York:20260307T100000', 'SUMMARY:Daily standup',
                  'RRULE:FREQ=DAILY;COUNT=4', 'EXDATE;TZID=America/New_York:20260309T090000',
                  'RDATE;TZID=America/New_York:20260312T090000')
    moved = item('standup', 'DTSTART;TZID=America/New_York:20260308T110000',
                 'DTEND;TZID=America/New_York:20260308T120000',
                 'RECURRENCE-ID;TZID=America/New_York:20260308T090000', 'SUMMARY:Moved standup')
    cancelled = item('standup', 'DTSTART;TZID=America/New_York:20260310T090000', None,
                     'RECURRENCE-ID;TZID=America/New_York:20260310T090000', 'STATUS:CANCELLED')
    return envelope(master, moved, cancelled)


def source(store, timezone_name='America/New_York'):
    return calendar.save_source(store, {'name': 'Recurring calendar', 'kind': 'ics', 'timezone': timezone_name})


def window():
    return {'window_start': '2026-03-01T00:00:00+00:00', 'window_end': '2027-03-01T00:00:00+00:00'}


def test_daily_recurrence_preserves_wall_time_across_dst_and_applies_exceptions():
    events, warnings = calendar.parse_ics(recurring_calendar(), 'America/New_York', **window())
    assert warnings == []
    assert len(events) == 3
    assert [row['start'] for row in events] == [
        '2026-03-07T14:00:00+00:00',
        '2026-03-08T15:00:00+00:00',
        '2026-03-12T13:00:00+00:00',
    ]
    assert [row['title'] for row in events] == ['Daily standup', 'Moved standup', 'Daily standup']
    assert events[0]['end'] == '2026-03-07T15:00:00+00:00'
    assert events[1]['end'] == '2026-03-08T16:00:00+00:00'
    assert events[2]['end'] == '2026-03-12T14:00:00+00:00'
    assert events[1]['overridden'] is True
    assert all(row['recurring'] is True for row in events)
    assert all(row['recurrence_unexpanded'] is False for row in events)
    assert len({row['id'] for row in events}) == 3
    assert '2026-03-08T09:00:00-04:00' in events[1]['id']
    assert not any('2026-03-09' in row['id'] or '2026-03-10' in row['id'] for row in events)


def test_weekly_monthly_and_yearly_rules_expand_deterministically():
    weekly = item('weekly', 'DTSTART:20260105T090000Z', 'DTEND:20260105T100000Z',
                  'SUMMARY:Weekdays', 'RRULE:FREQ=WEEKLY;COUNT=5;BYDAY=MO,WE')
    monthly = item('monthly', 'DTSTART:20260131T120000Z', 'DTEND:20260131T123000Z',
                   'SUMMARY:Month end', 'RRULE:FREQ=MONTHLY;COUNT=3;BYMONTHDAY=-1')
    yearly = item('yearly', 'DTSTART;VALUE=DATE:20260115', 'DTEND;VALUE=DATE:20260116',
                  'SUMMARY:Annual', 'RRULE:FREQ=YEARLY;COUNT=2;BYMONTH=1;BYMONTHDAY=15')
    events, warnings = calendar.parse_ics(envelope(weekly, monthly, yearly), 'UTC',
                                         '2026-01-01T00:00:00+00:00', '2027-01-01T00:00:00+00:00')
    assert warnings == []
    weekly_rows = [row for row in events if row['uid'] == 'weekly']
    assert [row['start'] for row in weekly_rows] == [
        '2026-01-05T09:00:00+00:00', '2026-01-07T09:00:00+00:00',
        '2026-01-12T09:00:00+00:00', '2026-01-14T09:00:00+00:00',
        '2026-01-19T09:00:00+00:00']
    monthly_rows = [row for row in events if row['uid'] == 'monthly']
    assert [row['start'][:10] for row in monthly_rows] == ['2026-01-31', '2026-02-28', '2026-03-31']
    annual = [row for row in events if row['uid'] == 'yearly']
    assert len(annual) == 1
    assert annual[0]['all_day'] is True
    assert annual[0]['start'] == '2026-01-15'
    assert annual[0]['end'] == '2026-01-16'


def test_all_day_recurrence_uses_exclusive_end_and_date_exclusions():
    master = item('holiday', 'DTSTART;VALUE=DATE:20260925', 'DTEND;VALUE=DATE:20260927',
                  'SUMMARY:Two day event', 'RRULE:FREQ=DAILY;COUNT=3',
                  'EXDATE;VALUE=DATE:20260926', 'RDATE;VALUE=DATE:20260930')
    events, _ = calendar.parse_ics(envelope(master), 'Europe/Berlin',
                                   '2026-09-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')
    assert [row['start'] for row in events] == ['2026-09-25', '2026-09-27', '2026-09-30']
    assert [row['end'] for row in events] == ['2026-09-27', '2026-09-29', '2026-10-02']
    assert all(row['all_day'] for row in events)


def test_interval_until_and_unmatched_override_are_bounded_truthfully():
    master = item('bounded', 'DTSTART:20260101T090000Z', 'DTEND:20260101T100000Z',
                  'SUMMARY:Bounded series', 'RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20260106T090000Z')
    unmatched = item('bounded', 'DTSTART:20260110T110000Z', 'DTEND:20260110T120000Z',
                     'RECURRENCE-ID:20260110T090000Z', 'SUMMARY:Outside rule')
    events, warnings = calendar.parse_ics(envelope(master, unmatched), 'UTC',
                                         '2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00')
    assert [row['start'] for row in events] == [
        '2026-01-01T09:00:00+00:00',
        '2026-01-03T09:00:00+00:00',
        '2026-01-05T09:00:00+00:00',
    ]
    assert warnings == [{'uid': 'bounded', 'unmatched_overrides': ['2026-01-10T09:00:00+00:00']}]
    assert all(row['title'] == 'Bounded series' for row in events)
    assert not any(row.get('overridden') for row in events)


@pytest.mark.parametrize('rule,match', [
    ('FREQ=HOURLY;COUNT=2', 'FREQ'),
    ('FREQ=DAILY;BYSETPOS=1', 'BYSETPOS'),
    ('FREQ=DAILY;COUNT=0', 'bounds'),
    ('FREQ=DAILY;INTERVAL=no', 'integers'),
])
def test_unsupported_or_unbounded_rule_parts_fail_closed(rule, match):
    content = envelope(item('bad-rule', 'DTSTART:20260101T090000Z', 'DTEND:20260101T100000Z',
                            'RRULE:' + rule))
    with pytest.raises(PeopleError, match=match):
        calendar.parse_ics(content, 'UTC', '2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00')


def test_window_bounds_value_types_and_duplicate_overrides_are_rejected():
    with pytest.raises(PeopleError, match='at most 366 days'):
        calendar.parse_ics(recurring_calendar(), 'UTC', '2026-01-01T00:00:00+00:00', '2027-01-03T00:00:00+00:00')
    mismatch = envelope(item('dates', 'DTSTART;VALUE=DATE:20260101', 'DTEND;VALUE=DATE:20260102',
                             'RRULE:FREQ=DAILY;COUNT=2', 'EXDATE:20260101T000000Z'))
    with pytest.raises(PeopleError, match='value type differs'):
        calendar.parse_ics(mismatch, 'UTC')
    master = item('duplicate', 'DTSTART:20260101T090000Z', 'DTEND:20260101T100000Z', 'RRULE:FREQ=DAILY;COUNT=2')
    override = item('duplicate', 'DTSTART:20260102T110000Z', 'DTEND:20260102T120000Z', 'RECURRENCE-ID:20260102T090000Z')
    with pytest.raises(PeopleError, match='Duplicate recurrence override'):
        calendar.parse_ics(envelope(master, override, override), 'UTC')


def test_upload_persists_canonical_occurrences_and_daily_window_coverage(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    sync = calendar.upload(store, row['id'], {'content': recurring_calendar(), 'revision': 1, **window()})
    assert sync['state'] == 'synced'
    assert sync['coverage'] == 'available_snapshot'
    assert sync['events'] == 3
    assert sync['start'] == window()['window_start']
    assert sync['end'] == window()['window_end']
    march7 = calendar.daily(store, '2026-03-07', 'America/New_York')
    assert march7['coverage'] == 'available_snapshot'
    assert march7['events'][0]['title'] == 'Daily standup'
    march8 = calendar.daily(store, '2026-03-08', 'America/New_York')
    assert march8['events'][0]['title'] == 'Moved standup'
    assert calendar.daily(store, '2026-03-09', 'America/New_York')['events'] == []
    assert calendar.daily(store, '2026-03-10', 'America/New_York')['events'] == []
    assert calendar.daily(store, '2026-03-12', 'America/New_York')['events'][0]['uid'] == 'standup'
    outside = calendar.daily(store, '2027-03-02', 'UTC')
    assert outside['events'] == []
    assert outside['coverage'] == 'partial'
    assert outside['sources'][0]['review_coverage'] == 'unknown'
    with store.connect() as db:
        bodies = [json.loads(value) for value, in db.execute('SELECT body FROM calendar_events ORDER BY id')]
        assert len(bodies) == 3
        assert len({value['id'] for value in bodies}) == 3


def test_replay_and_changed_exception_set_replace_occurrences_without_resurrection(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    payload = {'content': recurring_calendar(), 'revision': 1, **window()}
    first = calendar.upload(store, row['id'], payload)
    assert calendar.upload(PeopleStore(tmp_path), row['id'], payload) == first
    changed = recurring_calendar().replace('EXDATE;TZID=America/New_York:20260309T090000\r\n', '')
    second = calendar.upload(store, row['id'], {**payload, 'content': changed})
    assert second['events'] == 4
    assert calendar.daily(store, '2026-03-09', 'America/New_York')['events'][0]['title'] == 'Daily standup'
    assert calendar.upload(store, row['id'], payload) == first
    assert calendar.daily(store, '2026-03-09', 'America/New_York')['events'][0]['title'] == 'Daily standup'


def test_provider_instances_keep_provider_identity_and_cancelled_instances_stay_hidden(tmp_path):
    store = PeopleStore(tmp_path)
    row = calendar.save_source(store, {'name': 'Provider', 'kind': 'google', 'credential_ref': 'TOKEN'})
    raw = {'id': 'provider-instance-1', 'recurringEventId': 'series-1', 'originalStartTime': {'dateTime': '2026-03-08T09:00:00-04:00'},
           'iCalUID': 'series@example', 'summary': 'Provider expansion',
           'start': {'dateTime': '2026-03-08T09:00:00-04:00'}, 'end': {'dateTime': '2026-03-08T10:00:00-04:00'}}
    active = calendar.provider_event(raw, 'google')
    cancelled = calendar.provider_event({**raw, 'id': 'provider-instance-2', 'status': 'cancelled'}, 'google')
    state = {'state': 'synced', 'coverage': 'available_snapshot', 'scope': 'provider_window',
             'start': '2026-03-01T00:00:00+00:00', 'end': '2026-04-01T00:00:00+00:00',
             'captured_at': datetime.now(timezone.utc).isoformat(), 'events': 2, 'warnings': []}
    calendar.persist(store, row, [active, cancelled], state)
    review = calendar.daily(store, '2026-03-08', 'America/New_York')
    assert [event['id'] for event in review['events']] == ['provider-instance-1']
    assert review['events'][0]['uid'] == 'series@example'


@pytest.mark.asyncio
async def test_real_http_recurring_upload_and_daily_exception_journey(tmp_path, monkeypatch, unused_tcp_port):
    monkeypatch.setenv('GIDEON_HOME', str(tmp_path))
    app = web.Application()
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', unused_tcp_port).start()
    root = f'http://127.0.0.1:{unused_tcp_port}/api/capabilities/communications/calendar'
    try:
        async with ClientSession() as client:
            created = await client.post(root + '/sources', json={'name': 'HTTP recurrence', 'kind': 'ics', 'timezone': 'America/New_York'})
            assert created.status == 201
            row = (await created.json())['source']
            uploaded = await client.post(root + '/sources/' + row['id'] + '/upload', json={'content': recurring_calendar(), 'revision': 1, **window()})
            assert uploaded.status == 200
            sync = (await uploaded.json())['sync']
            assert sync['events'] == 3
            moved = await client.get(root + '/daily', params={'date': '2026-03-08', 'timezone': 'America/New_York'})
            assert moved.status == 200
            body = await moved.json()
            assert body['events'][0]['title'] == 'Moved standup'
            excluded = await client.get(root + '/daily', params={'date': '2026-03-09', 'timezone': 'America/New_York'})
            assert (await excluded.json())['events'] == []
            invalid = await client.post(root + '/sources/' + row['id'] + '/upload', json={'content': recurring_calendar(), 'revision': 1, 'window_start': '2026-01-01T00:00:00+00:00', 'window_end': '2027-01-03T00:00:00+00:00'})
            assert invalid.status == 400
            assert 'at most 366 days' in (await invalid.json())['error']
    finally:
        await runner.cleanup()

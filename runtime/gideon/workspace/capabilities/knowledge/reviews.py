"""Source-grounded obligation reviews saved in the canonical knowledge store."""

import hashlib
import json
from datetime import date as calendar_date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gideon.workspace.capabilities.communications.store import PeopleStore, care
from .capture import CaptureError, CaptureInbox, request_key, text_field
from .topics import TopicTasks, page_bounds

SECTIONS = ('overdue_tasks', 'upcoming_tasks', 'undated_tasks', 'completed_activity', 'contacts_overdue', 'contacts_missing', 'recent_notes', 'unreviewed_captures')
LIMIT = 1000


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def window(period, date, timezone_name):
    if period not in ('daily', 'weekly'):
        raise CaptureError('Review period must be daily or weekly')
    try:
        day = calendar_date.fromisoformat(date)
        zone = ZoneInfo(timezone_name)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise CaptureError('Valid review date and IANA timezone required') from None
    try:
        start = datetime.combine(day - timedelta(days=6 if period == 'weekly' else 0), time.min, zone)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
    except OverflowError:
        raise CaptureError('Review date lies outside the supported calendar window') from None
    return day, zone, start, end


def parsed(value, zone):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=zone) if result.tzinfo is None else result.astimezone(zone)
    except (ValueError, TypeError):
        return None


class ReviewService:
    def __init__(self, store, home=None):
        self.store, self.db = store, store.db
        self.home = Path(home).resolve() if home is not None else CaptureInbox._runtime_home()
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS capability_knowledge_reviews (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, receipt TEXT);
            CREATE TRIGGER IF NOT EXISTS review_receipt_immutable BEFORE UPDATE ON capability_knowledge_reviews
            WHEN OLD.receipt IS NOT NULL BEGIN SELECT RAISE(ABORT,'review receipt is immutable'); END;
            CREATE UNIQUE INDEX IF NOT EXISTS knowledge_review_guid ON items(guid) WHERE substr(guid,1,7) = 'review:';
        ''')

    def assert_scope(self):
        if CaptureInbox._runtime_home() != self.home:
            raise CaptureError('Runtime home changed; restore the bound allocation before saving reviews', 409)

    def receipt(self, key):
        row = self.db.execute('SELECT receipt FROM capability_knowledge_reviews WHERE request_id=?', (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def preview(self, period, date, timezone='UTC'):
        day, zone, start, end = window(period, date, timezone)
        sections, scanned, truncated = {key: [] for key in SECTIONS}, {}, []
        sources = {key: 'available' for key in ('tasks', 'contacts', 'notes', 'captures')}
        limitations = ['Completed activity uses current done status and updated_at, not an immutable completion event.', 'Naive source timestamps are interpreted in the review timezone.']
        def rows(kind, values):
            scanned[kind] = min(len(values), LIMIT)
            if len(values) > LIMIT:
                truncated.append(kind)
            return values[:LIMIT]
        def add(section, kind, identity, title, detail, link):
            sections[section].append({'source_type': kind, 'source_id': identity, 'title': title, 'detail': detail, 'source_link': link})
        tasks = TopicTasks(self.home)._all_tasks() if (self.home / 'tasks').is_dir() else []
        for task in rows('tasks', tasks):
            updated = parsed(task.updated_at, zone)
            status = getattr(task.status, 'value', task.status)
            link = '#/tasks?open=' + task.id
            if status in ('cancelled', 'skipped'):
                continue
            if status == 'done':
                if updated and start <= updated < end:
                    add('completed_activity', 'task', task.id, task.title, 'Currently done; updated ' + updated.isoformat(), link)
                continue
            due = parsed(task.due, zone)
            if due is None:
                add('undated_tasks', 'task', task.id, task.title, 'No usable due date', link)
            elif due.date() < day:
                add('overdue_tasks', 'task', task.id, task.title, 'Due ' + due.isoformat(), link)
            elif due.date() <= day + timedelta(days=7):
                add('upcoming_tasks', 'task', task.id, task.title, 'Due ' + due.isoformat(), link)
        people_root = self.home / 'capabilities/communications'
        people = PeopleStore(people_root) if (people_root / 'people.sqlite3').is_file() else None
        for person in rows('contacts', people.people() if people else []):
            state = care(person, people.touchpoints(person['id']), zone.key, now=end - timedelta(microseconds=1))
            if state['state'] in ('overdue', 'missing'):
                section = 'contacts_overdue' if state['state'] == 'overdue' else 'contacts_missing'
                detail = f"{state['days_overdue']} days beyond contact cadence" if state['state'] == 'overdue' else 'No recorded contact history'
                add(section, 'person', person['id'], person['name'], detail, '#/capabilities/communications?person=' + person['id'])
        notes = self.db.execute("SELECT id,title,created_at,file_metadata FROM items WHERE item_type IN ('note','journal','fleeting') AND status='active' AND COALESCE(is_archived,0)=0 AND (guid IS NULL OR substr(guid,1,7) != 'review:') AND (file_metadata IS NULL OR json_extract(file_metadata,'$.review_snapshot') IS NULL) ORDER BY created_at DESC,id LIMIT ?", (LIMIT + 1,)).fetchall()
        for note in rows('notes', notes):
            metadata = json.loads(note['file_metadata'] or '{}')
            created = parsed(note['created_at'], zone)
            if not metadata.get('review_snapshot') and created and start <= created < end:
                add('recent_notes', 'knowledge', note['id'], note['title'], 'Created ' + created.isoformat(), '#/knowledge/item/' + note['id'])
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        captures = []
        if 'capability_knowledge_captures' in tables:
            exclusion = ' AND id NOT IN (SELECT capture_id FROM capability_knowledge_types WHERE receipt IS NOT NULL)' if 'capability_knowledge_types' in tables else ''
            captures = self.db.execute('SELECT id,original_text,captured_at FROM capability_knowledge_captures WHERE destination_id IS NULL' + exclusion + ' ORDER BY captured_at,id LIMIT ?', (LIMIT + 1,)).fetchall()
        for capture in rows('captures', captures):
            captured = parsed(capture['captured_at'], zone)
            if captured and captured < end:
                add('unreviewed_captures', 'capture', capture['id'], capture['original_text'][:150] or 'Voice capture', 'Captured ' + captured.isoformat(), '#/capabilities/knowledge/capture?capture=' + capture['id'])
        for values in sections.values():
            values.sort(key=lambda row: (row['source_type'], row['source_id']))
        result = {'period': period, 'date': day.isoformat(), 'timezone': zone.key, 'window_start': start.isoformat(), 'window_end': end.isoformat(),
                  'sections': sections, 'sources': sources, 'scanned': scanned, 'truncated': truncated, 'limitations': limitations}
        return {**result, 'preview_id': digest(result)}

    def save(self, body, *, scheduled=False):
        if not isinstance(body, dict) or set(body) != {'request_id', 'period', 'date', 'timezone', 'preview_id', 'reflection'}:
            raise CaptureError('Review save requires request_id, period, date, timezone, preview_id and reflection only')
        key = request_key(body['request_id'])
        text_field(body['reflection'], 'reflection', 100000, required=False)
        payload = packed({**body, 'scheduled': scheduled})
        previous = self.db.execute('SELECT * FROM capability_knowledge_reviews WHERE request_id=?', (key,)).fetchone()
        if previous:
            if previous['payload'] != payload:
                raise CaptureError('Review request already belongs to different input', 409)
            if previous['receipt']:
                return json.loads(previous['receipt'])
        self.assert_scope()
        snapshot = self.preview(body['period'], body['date'], body['timezone'])
        if snapshot['preview_id'] != body['preview_id']:
            raise CaptureError('Review sources changed; preview again before saving', 409)
        if not previous:
            self.db.execute('INSERT OR IGNORE INTO capability_knowledge_reviews VALUES (?,?,NULL)', (key, payload))
            self.db.commit()
            reserved = self.db.execute('SELECT * FROM capability_knowledge_reviews WHERE request_id=?', (key,)).fetchone()
            if reserved['payload'] != payload:
                raise CaptureError('Review request already belongs to different input', 409)
            if reserved['receipt']:
                return json.loads(reserved['receipt'])
        title = body['period'].title() + ' review · ' + body['date']
        lines = [title, '', f"Timezone: {body['timezone']}", '', 'Reflection', body['reflection'] or '(No reflection recorded)', '']
        for section, values in snapshot['sections'].items():
            lines.extend(['## ' + section.replace('_', ' ').title(), *[f"- [{row['title']}]({row['source_link']}) — {row['detail']}" for row in values], ''])
        lines.extend(snapshot['limitations'])
        if snapshot['truncated']:
            lines.append('Source scan limit reached: ' + ', '.join(snapshot['truncated']))
        row = self.db.execute('SELECT id FROM items WHERE guid=?', ('review:' + key,)).fetchone()
        identity = row[0] if row else self.store.create_typed_item(item_type='note', title=title, content='\n'.join(lines), guid='review:' + key,
            extra={'file_metadata': {'review_snapshot': snapshot, 'scheduled': scheduled}})
        if identity is None:
            recovered = self.db.execute('SELECT id FROM items WHERE guid=?', ('review:' + key,)).fetchone()
            if recovered is None:
                raise CaptureError('Canonical review destination was not persisted', 409)
            identity = recovered[0]
        receipt = {'request_id': key, 'destination_id': identity, 'source_link': '#/knowledge/item/' + identity, 'preview_id': snapshot['preview_id'],
                   'period': body['period'], 'date': body['date'], 'timezone': body['timezone'], 'scheduled': scheduled, 'created_at': datetime.now(timezone.utc).isoformat()}
        self.db.execute('UPDATE capability_knowledge_reviews SET receipt=? WHERE request_id=? AND receipt IS NULL', (packed(receipt), key))
        self.db.commit()
        return self.receipt(key)

    def list(self, limit=20, offset=0):
        page_bounds(limit, offset)
        total = self.db.execute('SELECT count(*) FROM capability_knowledge_reviews WHERE receipt IS NOT NULL').fetchone()[0]
        items = [json.loads(row[0]) for row in self.db.execute('SELECT receipt FROM capability_knowledge_reviews WHERE receipt IS NOT NULL ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset))]
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset, 'next_offset': offset + limit if offset + limit < total else None}

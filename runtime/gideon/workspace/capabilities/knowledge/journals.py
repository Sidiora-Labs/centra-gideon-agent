"""Date-keyed journals remain canonical knowledge items with reviewed activity drafts."""

import json
from .capture import CaptureError, request_key, text_field
from .reviews import ReviewService, digest, packed, window

COLUMNS = ('title', 'content', 'file_metadata', 'item_type', 'status', 'is_archived')


class DateJournals:
    def __init__(self, store, home=None):
        self.store, self.db = store, store.db
        self.reviews = ReviewService(store, home)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS capability_knowledge_journal_mutations (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, journal_key TEXT NOT NULL, receipt TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS journal_pending_key ON capability_knowledge_journal_mutations(journal_key) WHERE receipt IS NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS journal_date_guid ON items(guid) WHERE substr(guid,1,13)='date_journal:';
            CREATE TRIGGER IF NOT EXISTS journal_receipt_immutable BEFORE UPDATE ON capability_knowledge_journal_mutations
            WHEN OLD.receipt IS NOT NULL BEGIN SELECT RAISE(ABORT,'journal receipt is immutable'); END;
        ''')

    def key(self, date, timezone):
        day, zone, _, _ = window('daily', date, timezone)
        return 'date_journal:' + digest([day.isoformat(), zone.key])

    def raw(self, key):
        row = self.db.execute('SELECT * FROM items WHERE guid=?', (key,)).fetchone()
        return dict(row) if row else None

    def projection(self, row):
        if row is None:
            return None
        meta = json.loads(row['file_metadata'] or '{}')
        return {'id': row['id'], 'title': row['title'], 'content': row['content'], 'revision': meta.get('journal_revision', 0),
                'fingerprint': digest({key: row[key] for key in COLUMNS}), 'source_link': '#/knowledge/item/' + row['id']}

    def get(self, date, timezone='UTC'):
        return {'date': date, 'timezone': timezone, 'journal': self.projection(self.raw(self.key(date, timezone)))}

    def draft(self, date, timezone='UTC'):
        existing = self.raw(self.key(date, timezone))
        snapshot = self.reviews.preview('daily', date, timezone)
        sources = [row for section in ('completed_activity', 'recent_notes') for row in snapshot['sections'][section]
                   if existing is None or row['source_id'] != existing['id']]
        result = {'date': date, 'timezone': timezone, 'sources': sources, 'limitations': snapshot['limitations'], 'truncated': snapshot['truncated']}
        content = '\n'.join([f'Activity for {date} ({timezone})', '', *[f"- [{row['title']}]({row['source_link']}) — {row['detail']}" for row in sources]])
        return {**result, 'content': content, 'preview_id': digest(result)}

    def save(self, body):
        required = {'request_id', 'date', 'timezone', 'revision', 'fingerprint', 'title', 'content', 'preview_id'}
        if not isinstance(body, dict) or set(body) != required:
            raise CaptureError('Journal save requires request_id, date, timezone, revision, fingerprint, title, content and preview_id only')
        key, request = self.key(body['date'], body['timezone']), request_key(body['request_id'])
        if type(body['revision']) is not int or body['revision'] < 0:
            raise CaptureError('Journal revision must be a nonnegative integer')
        for field, maximum, required_value in (('title', 300, True), ('content', 100000, True), ('fingerprint', 128, False), ('preview_id', 128, False)):
            text_field(body[field], field, maximum, required=required_value)
        payload = packed(body)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior = self.db.execute('SELECT * FROM capability_knowledge_journal_mutations WHERE request_id=?', (request,)).fetchone()
            if prior:
                if prior['payload'] != payload:
                    raise CaptureError('Journal request belongs to different input', 409)
                if prior['receipt']:
                    self.db.commit()
                    return json.loads(prior['receipt'])
            self.reviews.assert_scope()
            row = self.raw(key)
            meta = json.loads(row['file_metadata'] or '{}') if row else {}
            recovered = meta.get('journal_last_request') == request
            initial_pending = prior and row and body['revision'] == 0 and not meta.get('journal_revision') and row['title'] == body['title'] and row['content'] == body['content']
            approved = None
            if not recovered:
                current = self.projection(row)
                if not initial_pending and ((current['revision'] if current else 0) != body['revision'] or (current['fingerprint'] if current else '') != body['fingerprint']):
                    raise CaptureError('Journal changed in its canonical editor; reload before saving', 409)
                if row and (row['item_type'] != 'journal' or row['status'] != 'active' or row['is_archived']):
                    raise CaptureError('Restore the canonical journal before editing', 409)
                if body['preview_id']:
                    approved = self.draft(body['date'], body['timezone'])
                    if approved['preview_id'] != body['preview_id']:
                        raise CaptureError('Activity sources changed; review the draft again', 409)
            if not prior:
                pending = self.db.execute('SELECT request_id FROM capability_knowledge_journal_mutations WHERE journal_key=? AND receipt IS NULL', (key,)).fetchone()
                if pending:
                    raise CaptureError('An earlier journal write must be retried first', 409)
                self.db.execute('INSERT INTO capability_knowledge_journal_mutations VALUES (?,?,?,NULL)', (request, payload, key))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        if not recovered:
            metadata = {**meta, 'journal_date': body['date'], 'journal_timezone': body['timezone'], 'original_at': body['date'],
                        'journal_revision': body['revision'] + 1, 'journal_last_request': request}
            if body['preview_id']:
                metadata['journal_activity_draft'] = approved
            if row:
                changed = self.store.update_item(row['id'], title=body['title'], content=body['content'], file_metadata=metadata,
                    expected={field: row[field] for field in COLUMNS})
                if not changed:
                    latest = self.raw(key)
                    if not latest or json.loads(latest['file_metadata'] or '{}').get('journal_last_request') != request:
                        self.db.execute('DELETE FROM capability_knowledge_journal_mutations WHERE request_id=? AND receipt IS NULL', (request,))
                        self.db.commit()
                        raise CaptureError('Canonical journal changed during save; original content retained', 409)
            else:
                identity = self.store.create_typed_item(item_type='journal', title=body['title'], content=body['content'], guid=key, extra={'file_metadata': metadata})
                if identity is None:
                    candidate = self.raw(key)
                    if candidate and candidate['title'] == body['title'] and candidate['content'] == body['content'] and not json.loads(candidate['file_metadata'] or '{}').get('journal_revision'):
                        self.store.update_item(candidate['id'], file_metadata=metadata, expected={field: candidate[field] for field in COLUMNS})
        row = self.raw(key)
        if not row or json.loads(row['file_metadata'] or '{}').get('journal_last_request') != request:
            raise CaptureError('Canonical journal write is pending; retry the same request', 409)
        receipt = {'request_id': request, 'date': body['date'], 'timezone': body['timezone'], **self.projection(row)}
        self.db.execute('UPDATE capability_knowledge_journal_mutations SET receipt=? WHERE request_id=? AND receipt IS NULL', (packed(receipt), request))
        self.db.commit()
        return json.loads(self.db.execute('SELECT receipt FROM capability_knowledge_journal_mutations WHERE request_id=?', (request,)).fetchone()[0])

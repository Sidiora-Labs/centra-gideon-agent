"""Immutable structured context bound to canonical creative work revisions."""
import hashlib
import json
from datetime import datetime, timezone

from .store import CatalogError, identifier, integer, keys, text


FAMILIES = ('canon', 'cast', 'scene', 'pov', 'arc', 'world', 'research', 'comic')
CHECK_FAMILY = {
    'naming.': 'canon', 'roster.': 'canon', 'character.': 'canon',
    'relationships.': 'canon', 'objects.': 'canon', 'continuity.': 'canon',
    'cast.': 'cast', 'scene.': 'scene', 'visual.': 'scene',
    'sensory.': 'scene', 'narration.': 'scene', 'emotion.': 'scene',
    'pacing.': 'scene', 'pov.': 'pov', 'endings.': 'pov',
    'arc.': 'arc', 'plot.': 'arc', 'theme.': 'arc', 'chekhov.': 'arc',
    'world.': 'world', 'research.': 'research', 'comic.': 'comic',
}


def family_for(check_id):
    return next((family for prefix, family in CHECK_FAMILY.items() if check_id.startswith(prefix)), None)


def _array(value, name, cap=500):
    if not isinstance(value, list) or len(value) > cap:
        raise CatalogError(f'{name} must be an array with at most {cap} entries')
    return value


def _objects(value, name, required=()):
    result = []
    for index, item in enumerate(_array(value, name)):
        if not isinstance(item, dict):
            raise CatalogError(f'{name}[{index}] must be an object')
        missing = [field for field in required if not isinstance(item.get(field), str) or not item[field].strip()]
        if missing:
            raise CatalogError(f'{name}[{index}] requires {", ".join(missing)}')
        result.append(item)
    return result


def validate_context(family, value):
    if family not in FAMILIES:
        raise CatalogError('Unknown editorial context family')
    if not isinstance(value, dict):
        raise CatalogError('Editorial context must be a JSON object')
    if len(json.dumps(value, ensure_ascii=False)) > 500000:
        raise CatalogError('Editorial context exceeds 500000 characters')
    if family == 'canon':
        keys(value, {'characters', 'objects', 'rules'})
        _objects(value.get('characters', []), 'characters', ('id', 'name'))
        _objects(value.get('objects', []), 'objects', ('id', 'name'))
        _array(value.get('rules', []), 'rules')
    elif family == 'cast':
        keys(value, {'characters'})
        _objects(value.get('characters', []), 'characters', ('id', 'name'))
    elif family == 'scene':
        keys(value, {'scenes'})
        scenes = _objects(value.get('scenes', []), 'scenes', ('id',))
        for index, scene in enumerate(scenes):
            start, end = scene.get('start'), scene.get('end')
            if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool) or start < 0 or end <= start:
                raise CatalogError(f'scenes[{index}] requires integer start before end')
    elif family == 'pov':
        keys(value, {'policy', 'allowed', 'transitions'})
        text(value.get('policy', ''), 10000)
        _array(value.get('allowed', []), 'allowed')
        _objects(value.get('transitions', []), 'transitions')
    elif family == 'arc':
        keys(value, {'arcs', 'themes', 'ticking_clock', 'reader_map'})
        _objects(value.get('arcs', []), 'arcs', ('id', 'name'))
        _array(value.get('themes', []), 'themes')
        _array(value.get('reader_map', []), 'reader_map')
    elif family == 'world':
        keys(value, {'rules', 'places'})
        _array(value.get('rules', []), 'rules')
        _objects(value.get('places', []), 'places', ('id', 'name'))
    elif family == 'research':
        keys(value, {'claims'})
        _objects(value.get('claims', []), 'claims', ('id', 'claim', 'source'))
    else:
        keys(value, {'pages'})
        pages = _objects(value.get('pages', []), 'pages')
        for index, page in enumerate(pages):
            if not isinstance(page.get('number'), int) or isinstance(page.get('number'), bool) or page['number'] < 1:
                raise CatalogError(f'pages[{index}] requires a positive integer number')
            _objects(page.get('panels', []), f'pages[{index}].panels')
    return value


class EditorialContextStore:
    def __init__(self, works):
        self.works = works
        with works.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS editorial_context_bindings(
                    work_id TEXT, family TEXT, revision INTEGER, payload_hash TEXT,
                    record TEXT, PRIMARY KEY(work_id,family,revision));
                CREATE TABLE IF NOT EXISTS editorial_context_requests(
                    id TEXT PRIMARY KEY, payload_hash TEXT, record TEXT);
            ''')

    def bind(self, work_id, payload):
        keys(payload, {'request_id', 'work_revision', 'family', 'schema_version', 'data'})
        request_id = identifier(payload.get('request_id'))
        family = payload.get('family')
        if family not in FAMILIES:
            raise CatalogError('Unknown editorial context family')
        if integer(payload.get('schema_version')) != 1:
            raise CatalogError('Unsupported editorial context schema version')
        data = validate_context(family, payload.get('data'))
        digest = hashlib.sha256(json.dumps({'work_id': identifier(work_id), **payload}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.works.connection() as db:
            work = self.works._work(db, work_id)
            prior = db.execute('SELECT payload_hash,record FROM editorial_context_requests WHERE id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise CatalogError('Editorial context request conflict', 409)
                return json.loads(prior[1])
            if work['revision'] != integer(payload.get('work_revision')):
                raise CatalogError('Work changed; reload', 409)
            revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM editorial_context_bindings WHERE work_id=? AND family=?', (work_id, family)).fetchone()[0]
        slug = 'creative-context-' + hashlib.sha256(f'{work_id}:{family}:{revision}'.encode()).hexdigest()[:48]
        encoded = json.dumps({'schema_version': 1, 'family': family, 'data': data}, sort_keys=True, ensure_ascii=False)
        artifact = self.works.artifacts.get(slug, version=1)
        if artifact is None:
            artifact = self.works.artifacts.create(name=f'{family.title()} context revision {revision}', slug=slug, kind='json', content=encoded, description=digest, readonly=True)
        if artifact.slug != slug or artifact.content != encoded or not artifact.readonly:
            raise CatalogError('Canonical context artifact conflicts with binding', 409)
        record = {'work_id': work_id, 'family': family, 'revision': revision, 'schema_version': 1,
                  'artifact_id': slug, 'artifact_version': 1, 'work_revision': work['revision'],
                  'created_at': datetime.now(timezone.utc).isoformat()}
        with self.works.connection() as db:
            current = self.works._work(db, work_id)
            if current['revision'] != work['revision']:
                raise CatalogError('Work changed while binding editorial context', 409)
            prior = db.execute('SELECT payload_hash,record FROM editorial_context_requests WHERE id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise CatalogError('Editorial context request conflict', 409)
                return json.loads(prior[1])
            db.execute('INSERT INTO editorial_context_bindings VALUES(?,?,?,?,?)', (work_id, family, revision, digest, json.dumps(record)))
            db.execute('INSERT INTO editorial_context_requests VALUES(?,?,?)', (request_id, digest, json.dumps(record)))
        return record

    def list(self, work_id):
        with self.works.connection() as db:
            self.works._work(db, work_id)
            rows = [json.loads(row[0]) for row in db.execute('SELECT record FROM editorial_context_bindings WHERE work_id=? ORDER BY family,revision DESC', (work_id,))]
        latest = {}
        for row in rows:
            latest.setdefault(row['family'], row)
        return {'families': [{'family': family, **self._state(latest.get(family))} for family in FAMILIES]}

    def _state(self, record):
        if record is None:
            return {'status': 'missing', 'reason': 'no_canonical_context_binding'}
        artifact = self.works.artifacts.get(record['artifact_id'], version=record['artifact_version'])
        if artifact is None:
            return {**record, 'status': 'missing', 'reason': 'bound_artifact_missing'}
        try:
            value = json.loads(artifact.content)
            if value.get('schema_version') != record['schema_version'] or value.get('family') != record['family']:
                raise ValueError()
            validate_context(record['family'], value.get('data'))
        except (ValueError, TypeError, json.JSONDecodeError, CatalogError):
            return {**record, 'status': 'invalid', 'reason': 'bound_artifact_invalid'}
        return {**record, 'status': 'available', 'data': value['data']}

    def current(self, work_id, family):
        if family not in FAMILIES:
            return {'status': 'not_required'}
        with self.works.connection() as db:
            self.works._work(db, work_id)
            row = db.execute('SELECT record FROM editorial_context_bindings WHERE work_id=? AND family=? ORDER BY revision DESC LIMIT 1', (work_id, family)).fetchone()
        return self._state(json.loads(row[0]) if row else None)

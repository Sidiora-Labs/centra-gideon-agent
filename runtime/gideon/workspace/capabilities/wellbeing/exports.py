"""Versioned wellbeing snapshots with only referenced canonical source attachments."""
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from gideon.workspace.artifacts.native import NativeArtifactProvider
from .store import MeasurementError, MeasurementStore, text

SCHEMA = 'gideon.wellbeing-export'
VERSION = 1
MAX_BYTES = 64 * 1024 * 1024
TABLES = {
    'measurements': [('history', 'revisions', 'data'), ('shared_sources', 'shared_health_imports', 'data'), ('shared_links', 'shared_health_links', None), ('shared_identity', 'shared_health_meta', None)],
    'laboratory': [('history', 'lab_revisions', 'data'), ('imports', 'lab_imports', 'receipt'), ('source_identities', 'lab_sources', None)],
    'apple': [('metrics', 'apple_metrics', 'data'), ('imports', 'apple_imports', 'receipt')],
    'substances': [('entries', 'substance_entries', 'data'), ('presets', 'substance_presets', 'data')],
    'genome': [('sources', 'genome_sources', 'data'), ('variants', 'genome_variants', 'data')],
    'interventions': [('plans', 'intervention_plans', 'data'), ('records', 'intervention_records', 'data')],
    'cognition': [('sessions', 'cognitive_sessions', 'data')],
    'memory': [('cards', 'memory_card_revisions', 'data')],
    'life': [('history', 'life_calendar_revisions', 'data'), ('reminder_claims', 'life_reminder_claims', None)],
    'epigenetic': [('history', 'epigenetic_results', 'data')],
    'eye_prescriptions': [('history', 'eye_prescription_revisions', 'data')],
    'lifestyle_profiles': [('history', 'lifestyle_profile_revisions', 'data')],
    'body_composition': [('history', 'body_composition_revisions', 'data')],
}


def references(value):
    if isinstance(value, dict):
        if isinstance(value.get('artifact'), dict):
            yield value['artifact']
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


class ExportStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        self.artifacts = NativeArtifactProvider(root=self.path.parent.parent / 'artifacts')
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS wellbeing_exports(id TEXT PRIMARY KEY,data TEXT NOT NULL)')

    def _snapshot(self, db):
        available = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sections, counts = {}, {}
        section_bytes = 0
        for domain, tables in TABLES.items():
            sections[domain] = {}
            for key, table, column in tables:
                rows = []
                if table in available:
                    cursor = db.execute(f'SELECT * FROM {table} ORDER BY rowid')
                    columns = [entry[0] for entry in cursor.description]
                    for record in cursor:
                        values = dict(zip(columns, record))
                        section_bytes += len((values[column] if column else json.dumps(values, ensure_ascii=True)).encode())
                        if section_bytes > MAX_BYTES:
                            raise MeasurementError('Export exceeds64MiB; no partial export created', 413, 'too_large')
                        row = json.loads(values[column]) if column else values
                        if domain == 'cognition':
                            for trial in row['trials']:
                                trial.pop('expected', None)
                            if row['current_trial']:
                                row['current_trial'].pop('expected', None)
                        rows.append(row)
                sections[domain][key] = rows
            counts[domain] = sum(len(rows) for rows in sections[domain].values())
        attachments, seen, attachment_bytes = [], set(), 0
        for reference in references(sections):
            slug, version, filename = reference.get('slug'), reference.get('version'), reference.get('filename')
            if not isinstance(slug, str) or not re.fullmatch(r'(lab-source|apple-source|genome-source|memory-card|shared-health)-[a-f0-9]{64}', slug) or version != 1:
                raise MeasurementError('Export source artifact reference is unsupported', 409, 'conflict')
            identity = (slug, version, filename)
            if identity in seen:
                continue
            seen.add(identity)
            artifact = self.artifacts.get(slug, version=version)
            if artifact is None:
                raise MeasurementError('Referenced source artifact is unavailable', 404, 'not_found')
            descriptor = artifact.content.encode()
            if filename is not None:
                if not isinstance(filename, str) or not re.fullmatch(r'original@[a-f0-9]{64}\.(csv|json|xml|zip|fhir|tsv|vcf)', filename):
                    raise MeasurementError('Invalid original attachment filename', 409, 'conflict')
                folder = self.path.parent.parent / 'artifacts' / slug / 'versions'
                path = folder / filename
                if path.resolve().parent != folder.resolve() or path.is_symlink():
                    raise MeasurementError('Original attachment path is unsafe', 409, 'conflict')
                try:
                    if path.stat().st_size + attachment_bytes > MAX_BYTES:
                        raise MeasurementError('Export exceeds64MiB; no partial export created', 413, 'too_large')
                    raw = path.read_bytes()
                except OSError as exc:
                    raise MeasurementError('Original attachment unavailable', 404, 'not_found') from exc
            else:
                raw = descriptor
            if hashlib.sha256(raw).hexdigest() != reference.get('sha256'):
                raise MeasurementError('Source attachment hash mismatch', 409, 'conflict')
            attachment_bytes += len(raw) + (len(descriptor) if filename else 0)
            if attachment_bytes > MAX_BYTES:
                raise MeasurementError('Export exceeds64MiB; no partial export created', 413, 'too_large')
            attachments.append(dict(reference=reference, name=artifact.name, kind=artifact.kind, descriptor=artifact.content, descriptor_sha256=hashlib.sha256(descriptor).hexdigest(), content_base64=base64.b64encode(raw).decode(), bytes=len(raw)))
        snapshot = dict(schema=SCHEMA, version=VERSION, generated_at=datetime.now(timezone.utc).isoformat(), sections=sections, attachments=attachments, counts=counts, omitted=['operation_retry_receipts', 'private_exercise_expected_answers', 'external_trigger_definitions', 'inbox_items'])
        raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
        if len(raw) > MAX_BYTES:
            raise MeasurementError('Export exceeds64MiB; no partial export created', 413, 'too_large')
        return snapshot, raw, attachment_bytes

    def preview(self):
        with self.connection() as db:
            db.execute('BEGIN')
            snapshot, raw, size = self._snapshot(db)
        return dict(schema=SCHEMA, version=VERSION, counts=snapshot['counts'], attachment_count=len(snapshot['attachments']), attachment_bytes=size, export_bytes=len(raw), omitted=snapshot['omitted'])

    def create(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'request_id'}:
            raise MeasurementError('Export creation requires only request_id')
        request_id = text(payload['request_id'], 'request_id', 128)
        fingerprint = json.dumps(['wellbeing-export', VERSION])
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT payload,result FROM requests WHERE id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError('Request ID already used', 409, 'conflict')
                return json.loads(prior[1])
            snapshot, raw, _ = self._snapshot(db)
            sha = hashlib.sha256(raw).hexdigest()
            slug = 'health-export-' + sha
            filename = 'export@' + sha + '.json'
            metadata = dict(id=sha, schema=SCHEMA, version=VERSION, generated_at=snapshot['generated_at'], sha256=sha, bytes=len(raw), counts=snapshot['counts'], artifact=dict(slug=slug, version=1, filename=filename), attachment_count=len(snapshot['attachments']), omitted=snapshot['omitted'])
            descriptor = json.dumps(metadata, sort_keys=True)
            artifact = self.artifacts.get(slug, version=1)
            if artifact is None:
                artifact = self.artifacts.create(name='Wellbeing export ' + snapshot['generated_at'], content=descriptor, kind='document', source='import', slug=slug, readonly=True)
            if artifact.content != descriptor or not self.artifacts.store_version_file(slug, filename, raw):
                raise MeasurementError('Export artifact could not be persisted', 503, 'unavailable')
            if self._read(metadata) != raw:
                raise MeasurementError('Export artifact verification failed', 503, 'unavailable')
            encoded = json.dumps(metadata)
            db.execute('INSERT INTO wellbeing_exports VALUES(?,?)', (sha, encoded))
            db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, fingerprint, encoded))
            return metadata

    def get(self, identity):
        with self.connection() as db:
            row = db.execute('SELECT data FROM wellbeing_exports WHERE id=?', (identity,)).fetchone()
            if not row:
                raise MeasurementError('Wellbeing export not found', 404, 'not_found')
            return json.loads(row[0])

    def list_exports(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM wellbeing_exports ORDER BY json_extract(data,\'$.generated_at\') DESC LIMIT 500')]

    def _read(self, metadata):
        reference = metadata['artifact']
        try:
            raw = (self.path.parent.parent / 'artifacts' / reference['slug'] / 'versions' / reference['filename']).read_bytes()
        except OSError as exc:
            raise MeasurementError('Export attachment unavailable', 404, 'not_found') from exc
        if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
            raise MeasurementError('Export hash mismatch', 409, 'conflict')
        return raw

    def download(self, identity):
        return self._read(self.get(identity))

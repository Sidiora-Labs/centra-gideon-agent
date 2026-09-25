"""Source-preserving genome indexing and explicitly authored annotations."""

import hashlib
import json
import re
from datetime import datetime, timezone

from .labs import LabStore, digest
from .store import MeasurementError, text


def parse_genome(payload):
    if not isinstance(payload, dict) or set(payload) - {'filename', 'format', 'content', 'source', 'assembly', 'sample', 'preview_id', 'request_id'}:
        raise MeasurementError('Unexpected genome import fields')
    for key, limit in [('filename', 200), ('source', 256), ('content', 8 * 1024 * 1024)]:
        text(payload.get(key), key, limit)
    if len(payload['content'].encode()) > 8 * 1024 * 1024:
        raise MeasurementError('Genome input exceeds 8 MiB')
    assembly, format_ = payload.get('assembly'), payload.get('format')
    if assembly not in ('GRCh37', 'GRCh38') or format_ not in ('tsv', 'vcf'):
        raise MeasurementError('Supply assembly GRCh37/GRCh38 and format tsv/vcf')
    sample = text(payload.get('sample', ''), 'sample', 200, True)
    header, variants, seen = None, [], set()
    for line_number, line in enumerate(payload['content'].removeprefix('\ufeff').splitlines(), 1):
        if not line.strip() or line.startswith('##'):
            continue
        cells = line.split('\t')
        if header is None:
            candidate = cells[:]
            candidate[0] = candidate[0].lstrip('# ').lower()
            if format_ == 'tsv' and candidate == ['rsid', 'chromosome', 'position', 'genotype']:
                header = cells
                continue
            if format_ == 'vcf' and cells[:9] == ['#CHROM', 'POS', 'ID', 'REF', 'ALT', 'QUAL', 'FILTER', 'INFO', 'FORMAT'] and len(cells) >= 10:
                if len(set(cells[9:])) != len(cells[9:]):
                    raise MeasurementError('VCF sample names must be unique')
                if not sample and len(cells) == 10:
                    sample = cells[9]
                if sample not in cells[9:]:
                    raise MeasurementError('Select an explicit VCF sample from the header')
                header = cells
                continue
            if line.startswith('#'):
                continue
            raise MeasurementError(f'Line {line_number}: expected genome header')
        if line.startswith('#'):
            continue
        try:
            if len(cells) != len(header):
                raise ValueError('column count differs from header')
            if format_ == 'tsv':
                rsid, chromosome, position, genotype = cells
                reference, alternate = None, None
                if not re.fullmatch(r'[ACGTN-]{1,2}|[ACGTN.]+[/|][ACGTN.]+', genotype):
                    raise ValueError('unsupported TSV genotype')
            else:
                chromosome, position, rsid, reference, alternate = cells[:5]
                alleles = [reference, *alternate.split(',')]
                if not all(re.fullmatch('[ACGTN]+', allele) for allele in alleles):
                    raise ValueError('only explicit sequence VCF alleles are supported')
                keys = cells[8].split(':')
                values = cells[header.index(sample, 9)].split(':')
                if 'GT' not in keys or keys.index('GT') >= len(values):
                    raise ValueError('VCF sample requires GT')
                gt = values[keys.index('GT')]
                if not re.fullmatch(r'(\d+|\.)([/|](\d+|\.))*', gt):
                    raise ValueError('malformed VCF genotype')
                genotype = ''.join(token if token in ('/', '|', '.') else alleles[int(token)] for token in re.split(r'([/|])', gt))
            chromosome = chromosome.removeprefix('chr')
            if chromosome not in [str(n) for n in range(1, 23)] + ['X', 'Y', 'MT', 'M']:
                raise ValueError('unsupported chromosome')
            if not position.isdigit() or not 1 <= int(position) <= 1_000_000_000:
                raise ValueError('position must be a positive bounded integer')
            text(rsid, 'rsid', 256)
            row = dict(assembly=assembly, chromosome=chromosome, position=int(position), rsid=rsid, genotype=genotype, reference=reference, alternate=alternate, row_index=line_number)
            key = digest({key: value for key, value in row.items() if key != 'row_index'})
            if key not in seen:
                variants.append(row)
                seen.add(key)
            if len(variants) > 100000:
                raise ValueError('genome source exceeds 100000 variants')
        except (ValueError, IndexError) as exc:
            raise MeasurementError(f'Line {line_number}: {exc}') from exc
    if not variants:
        raise MeasurementError('Genome source has no supported variants')
    source_id = digest([payload['source'], assembly, sample, format_, payload['content']])
    preview_id = digest([source_id, payload['filename']])
    return source_id, preview_id, sample, variants


class GenomeStore(LabStore):
    def __init__(self, home):
        super().__init__(home)
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS genome_sources(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS genome_variants(id TEXT NOT NULL,revision INTEGER NOT NULL,source_id TEXT NOT NULL,chromosome TEXT NOT NULL,position INTEGER NOT NULL,rsid TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision));''')

    def preview(self, payload):
        source_id, preview_id, sample, rows = parse_genome(payload)
        with self.connection() as db:
            exists = db.execute('SELECT 1 FROM genome_sources WHERE id=?', (source_id,)).fetchone()
        return dict(preview_id=preview_id, source_id=source_id, sample=sample, row_count=len(rows), duplicates=len(rows) if exists else 0, variants=rows[:100])

    def commit(self, payload):
        source_id, preview_id, sample, rows = parse_genome(payload)
        if payload.get('preview_id') != preview_id:
            raise MeasurementError('Preview changed; preview again', 409, 'conflict')
        request_id = text(payload.get('request_id'), 'request_id', 128)
        fingerprint = digest(['genome-import', preview_id])
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT payload,result FROM requests WHERE id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError('Request ID already used', 409, 'conflict')
                return json.loads(prior[1])
            prior = db.execute('SELECT data FROM genome_sources WHERE id=?', (source_id,)).fetchone()
            if prior:
                receipt = dict(source=json.loads(prior[0]), added=0, duplicates=len(rows))
            else:
                slug = 'genome-source-' + source_id
                raw = payload['content'].encode()
                sha = hashlib.sha256(raw).hexdigest()
                descriptor = json.dumps({'format': payload['format'], 'sha256': sha, 'source': payload['source'], 'assembly': payload['assembly'], 'sample': sample}, sort_keys=True)
                artifact = self.artifacts.get(slug, version=1)
                if artifact is None:
                    artifact = self.artifacts.create(name=payload['filename'], content=descriptor, kind='document', source='import', slug=slug, readonly=True)
                if artifact.content != descriptor:
                    raise MeasurementError('Original artifact differs', 409, 'conflict')
                reference = dict(slug=slug, version=1, sha256=sha, filename=f'original@{sha}.{payload["format"]}')
                if not self.artifacts.store_version_file(slug, reference['filename'], raw):
                    raise MeasurementError('Original attachment unavailable')
                self._original(reference)
                now = datetime.now(timezone.utc).isoformat()
                source = dict(id=source_id, filename=payload['filename'], format=payload['format'], source=payload['source'], assembly=payload['assembly'], sample=sample, artifact=reference, variant_count=len(rows), created_at=now)
                db.execute('INSERT INTO genome_sources VALUES(?,?)', (source_id, json.dumps(source)))
                for row in rows:
                    identity = digest([source_id, row])
                    record = dict(row, id=identity, source_id=source_id, annotation='', annotation_source='', revision=1, created_at=now)
                    self._append_variant(db, record)
                receipt = dict(source=source, added=len(rows), duplicates=0)
            db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, fingerprint, json.dumps(receipt)))
            return receipt

    def _append_variant(self, db, row):
        db.execute('INSERT INTO genome_variants VALUES(?,?,?,?,?,?,?)', (row['id'], row['revision'], row['source_id'], row['chromosome'], row['position'], row['rsid'], json.dumps(row)))

    def list_sources(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM genome_sources ORDER BY id LIMIT 1000')]

    def get_source(self, identity):
        with self.connection() as db:
            row = db.execute('SELECT data FROM genome_sources WHERE id=?', (identity,)).fetchone()
            if not row:
                raise MeasurementError('Genome source not found', 404, 'not_found')
            return json.loads(row[0])

    def original(self, identity):
        return self._original(self.get_source(identity)['artifact'])

    def list_variants(self, source_id, *, chromosome=None, rsid=None, limit=100, offset=0):
        self.get_source(source_id)
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or not 0 <= offset <= 100000:
            raise MeasurementError('Invalid pagination')
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('''SELECT r.data FROM genome_variants r WHERE source_id=? AND revision=(SELECT MAX(s.revision) FROM genome_variants s WHERE s.id=r.id)
                AND (? IS NULL OR chromosome=?) AND (? IS NULL OR rsid=?) ORDER BY chromosome,position,id LIMIT ? OFFSET ?''', (source_id, chromosome, chromosome, rsid, rsid, limit, offset))]

    def _variant(self, db, identity):
        row = db.execute('SELECT data FROM genome_variants WHERE id=? ORDER BY revision DESC LIMIT 1', (identity,)).fetchone()
        if not row:
            raise MeasurementError('Genome variant not found', 404, 'not_found')
        return json.loads(row[0])

    def get_variant(self, identity):
        with self.connection() as db:
            return self._variant(db, identity)

    def annotate(self, identity, payload):
        if not isinstance(payload, dict) or set(payload) != {'request_id', 'revision', 'annotation', 'annotation_source'}:
            raise MeasurementError('Annotation requires request_id, revision, annotation and annotation_source')
        request_id = text(payload['request_id'], 'request_id', 128)
        annotation = text(payload['annotation'], 'annotation', 10000, True)
        source = text(payload['annotation_source'], 'annotation_source', 256)
        if type(payload['revision']) is not int:
            raise MeasurementError('Annotation revision must be an integer')
        fingerprint = digest(['genome-annotation', identity, payload])
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT payload,result FROM requests WHERE id=?', (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError('Request ID already used', 409, 'conflict')
                return json.loads(prior[1])
            row = self._variant(db, identity)
            if payload['revision'] != row['revision']:
                raise MeasurementError('Variant changed; reload', 409, 'conflict')
            row.update(annotation=annotation, annotation_source=source, revision=row['revision'] + 1)
            self._append_variant(db, row)
            db.execute('INSERT INTO requests VALUES(?,?,?)', (request_id, fingerprint, json.dumps(row)))
            return row

    def history(self, identity):
        with self.connection() as db:
            self._variant(db, identity)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM genome_variants WHERE id=? ORDER BY revision', (identity,))]

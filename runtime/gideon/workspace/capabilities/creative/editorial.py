"""Persistent source-pinned editorial runs and reviewed repair candidates."""
import hashlib
import json
from datetime import datetime, timezone
from .store import CatalogError, identifier, integer, keys
from .works import WorkStore
from .polishing import PolishingStore
from .prose_checks import CATALOG, SUPPORTED, scan
from .editorial_context import EditorialContextStore, family_for
from .context_checks import deterministic, model


class EditorialStore:
    def __init__(self, works: WorkStore):
        self.works = works
        self.polishing = PolishingStore(works)
        self.context = EditorialContextStore(works)
        with works.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS editorial_repairs(id TEXT PRIMARY KEY, payload_hash TEXT, record TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS editorial_runs(id TEXT PRIMARY KEY, work_id TEXT, payload_hash TEXT, record TEXT)')

    def catalog(self, work_id=None):
        result = []
        for check in CATALOG['checks']:
            family = family_for(check['id'])
            if check['id'] in SUPPORTED:
                availability = 'available'
            elif family and work_id and self.context.current(work_id, family)['status'] == 'available':
                availability = 'available'
            elif family:
                availability = 'requires_context'
            else:
                availability = 'configured_model_required'
            result.append({**check, 'availability': availability, 'context_family': family,
                           'language': 'English heuristics or configured model; Unicode source offsets'})
        return result

    def _run(self, db, id, run_id):
        self.works._work(db, id)
        row = db.execute('SELECT record FROM editorial_runs WHERE id=? AND work_id=?', (identifier(run_id), identifier(id))).fetchone()
        if row is None:
            raise CatalogError('Editorial run not found', 404)
        return json.loads(row[0])

    def get(self, id):
        with self.works.connection() as db:
            work = self.works._work(db, id)
            records = [json.loads(row[0]) for row in db.execute('SELECT record FROM editorial_runs WHERE work_id=? ORDER BY rowid DESC', (id,))]
        for run in records:
            artifact = self.works.artifacts.get(run['artifact_id'], version=run['artifact_version'])
            context_stale = []
            for binding in run.get('context_bindings', []):
                state = self.context.current(id, binding['family'])
                if state.get('status') != 'available' or state.get('revision') != binding['revision']:
                    context_stale.append(binding['family'])
            run.update(missing=artifact is None, stale=work['active_draft_id'] != run['draft_id'] or work['revision'] != run['work_revision'] or bool(context_stale),
                       stale_context_families=context_stale)
        return {'work_id': id, 'catalog': self.catalog(id), 'contexts': self.context.list(id)['families'],
                'runs': records, 'formula_version': 'editorial-context-v1'}

    def run(self, id, payload):
        import asyncio
        return asyncio.run(self.run_async(id, payload))

    async def run_async(self, id, payload):
        keys(payload, {'request_id', 'work_revision', 'check_ids', 'start', 'end'})
        request = identifier(payload.get('request_id'))
        run_id = hashlib.sha256((identifier(id) + ':' + request).encode()).hexdigest()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.works.connection() as db:
            work = self.works._work(db, id)
            prior = db.execute('SELECT payload_hash,record FROM editorial_runs WHERE id=?', (run_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise CatalogError('Editorial request conflict', 409)
                return json.loads(prior[1])
        revision = integer(payload.get('work_revision'))
        if work['revision'] != revision:
            raise CatalogError('Work changed; reload', 409)
        if not work['active_draft_id']:
            raise CatalogError('Save a canonical draft first')
        draft = self.works.read_draft(id, work['active_draft_id'])
        if draft['missing']:
            raise CatalogError('Canonical draft missing', 404)
        source = draft['text']
        start = integer(payload.get('start'), 0, len(source) - 1)
        end = integer(payload.get('end'), start + 1, len(source))
        if end - start > 20000:
            raise CatalogError('Select at most 20000 characters')
        selected = payload.get('check_ids', sorted(SUPPORTED))
        known = {c['id'] for c in CATALOG['checks']}
        if not isinstance(selected, list) or not 1 <= len(selected) <= 80 or any(not isinstance(c, str) or c not in known for c in selected) or len(selected) != len(set(selected)):
            raise CatalogError('Choose unique known editorial check IDs')
        results, findings, bindings = [], [], []
        catalog = {item['id']: item for item in CATALOG['checks']}
        for check in selected:
            definition = catalog[check]
            family = family_for(check)
            context = self.context.current(id, family) if family else {'status': 'not_required'}
            if family and context['status'] != 'available':
                results.append({'check_id': check, 'status': 'skipped', 'reason': f'{family}_context_{context["status"]}'})
                continue
            if family:
                binding = {key: context[key] for key in ('family', 'revision', 'schema_version', 'artifact_id', 'artifact_version')}
                if binding not in bindings:
                    bindings.append(binding)
            if check in SUPPORTED:
                hits = scan(check, source[start:end])
                for hit in hits:
                    hit['start'], hit['end'] = start + hit['start'], start + hit['end']
            elif definition['kind'] == 'deterministic':
                hits = [hit for hit in deterministic(check, source, context) if start <= hit['start'] < hit['end'] <= end]
            else:
                outcome = await model(definition, source, {'start': start, 'end': end}, context)
                if outcome['status'] != 'completed':
                    results.append({'check_id': check, 'status': outcome['status'], 'reason': outcome['reason']})
                    continue
                hits = outcome['findings']
            count = len(hits)
            for index, hit in enumerate(hits[:100]):
                findings.append({**hit, 'id': hashlib.sha256(f'{run_id}:{check}:{index}'.encode()).hexdigest(), 'check_id': check,
                                 'severity': definition['severity']})
            results.append({'check_id': check, 'status': 'completed', 'finding_count': count, 'truncated': count > 100})
        record = {'id': run_id, 'work_id': id, 'work_revision': revision, 'draft_id': draft['id'], 'artifact_id': draft['artifact_id'],
                  'artifact_version': draft['artifact_version'], 'coverage': {'start': start, 'end': end, 'total_characters': len(source)},
                  'context_bindings': bindings, 'created_at': datetime.now(timezone.utc).isoformat(), 'kind': 'mixed', 'results': results, 'findings': findings,
                  'readiness': 'review_required' if findings else 'incomplete' if any(r['status'] != 'completed' for r in results) or start != 0 or end != len(source) else 'selected_checks_clear'}
        with self.works.connection() as db:
            current = self.works._work(db, id)
            if current['revision'] != revision:
                raise CatalogError('Work changed during editorial run', 409)
            prior = db.execute('SELECT payload_hash,record FROM editorial_runs WHERE id=?', (run_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise CatalogError('Editorial request conflict', 409)
                return json.loads(prior[1])
            db.execute('INSERT INTO editorial_runs VALUES(?,?,?,?)', (run_id, id, digest, json.dumps(record)))
        return record

    async def repair(self, id, run_id, finding_id, payload):
        keys(payload, {'request_id', 'work_revision', 'replacement'})
        request = hashlib.sha256((identifier(id) + ':' + identifier(run_id) + ':' + identifier(finding_id) + ':' + identifier(payload.get('request_id'))).encode()).hexdigest()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.works.connection() as db:
            record = self._run(db, id, run_id)
            prior = db.execute('SELECT payload_hash,record FROM editorial_repairs WHERE id=?', (request,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise CatalogError('Repair request conflict', 409)
                return json.loads(prior[1])
            finding = next((f for f in record['findings'] if f['id'] == identifier(finding_id)), None)
            if finding is None:
                raise CatalogError('Editorial finding not found', 404)
            work = self.works._work(db, id)
        if work['revision'] != integer(payload.get('work_revision')) or work['revision'] != record['work_revision'] or work['active_draft_id'] != record['draft_id']:
            raise CatalogError('Finding source changed; run editorial checks again', 409)
        draft = self.works.read_draft(id, record['draft_id'])
        if draft['missing']:
            raise CatalogError('Finding source missing', 404)
        if draft['text'][finding['start']:finding['end']] != finding['quote']:
            raise CatalogError('Finding no longer matches canonical source', 409)
        proposal = await self.polishing.propose(id, {'request_id': request, 'revision': work['revision'], 'mode': 'authored',
            'start': finding['start'], 'end': finding['end'], 'instruction': finding['problem'], 'replacement': payload.get('replacement')})
        result = {'run_id': run_id, 'finding_id': finding_id, 'proposal': proposal}
        with self.works.connection() as db:
            prior = db.execute('SELECT payload_hash,record FROM editorial_repairs WHERE id=?', (request,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise CatalogError('Repair request conflict', 409)
                return json.loads(prior[1])
            db.execute('INSERT INTO editorial_repairs VALUES(?,?,?)', (request, digest, json.dumps(result)))
        return result

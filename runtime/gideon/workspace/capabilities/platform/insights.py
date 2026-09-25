"""Descriptive scorecards over canonical personal records and versioned artifacts."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from gideon.core.config.loader import config_dir
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.identity.goal_plans import GoalPlanStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementStore, MeasurementError
from gideon.workspace.capabilities.wellbeing.labs import LabStore
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore

TAG = 'personal-scorecard'
LIMITATION = 'Descriptive observations only; missing records remain unknown. Changes do not establish causes, clinical outcomes, or an overall health score.'


def artifacts(home):
    return NativeArtifactProvider(root=Path(home) / 'artifacts')


def snapshot(home=None):
    home = Path(home or config_dir())
    goals = GoalPlanStore(home / 'capabilities/identity/goals.sqlite3').list()
    measures, labs = MeasurementStore(home).list(limit=500), LabStore(home).list(limit=500)
    interventions = InterventionStore(home)
    plans = interventions.list_plans()
    rows = []
    for item in goals[:200]:
        goal, plan = item['goal'], item['plan']
        rows.append(dict(kind='goal', id=goal['id'], label=goal['title'], revision=goal['revision'], plan_revision=plan['revision'], unit=plan['unit'], target=plan['target_value'], observations=item['checkins'], velocity=item['velocity'], milestones_complete_ratio=item['milestones_complete_ratio'], sources=item['linked_sources'], href='#/capabilities/identity/goal-plans'))
    for kind, records, href in [('measurement', measures, '#/capabilities/wellbeing'), ('laboratory', labs, '#/capabilities/wellbeing/labs')]:
        groups = {}
        for record in records:
            label = record.get('analyte', record['kind'])
            groups.setdefault((label, record['unit']), []).append(record)
        for (label, unit), values in sorted(groups.items()):
            values.sort(key=lambda row: (datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00')), row['id']))
            observations = [dict(id=row['id'], revision=row['revision'], observed_at=row['observed_at'], values=row.get('values', {'value': row.get('value')}), source=row['source'], artifact=row.get('artifact')) for row in values]
            first, last = observations[0], observations[-1]
            delta = {key: value - first['values'][key] for key, value in last['values'].items()} if len(values) > 1 else None
            rows.append(dict(kind=kind, id=f'{kind}:{label}:{unit}', label=label, unit=unit, observations=observations, change=delta, href=href))
    as_of = datetime.now(timezone.utc).date().isoformat() + 'T23:59:59+00:00'
    for plan in plans[:200]:
        summary = interventions.summary(plan['id'], days=30, as_of=as_of)
        rows.append(dict(kind='intervention', id=plan['id'], label=plan['name'], revision=plan['revision'], source=plan['source'], summary=summary, observations=interventions.list_records(plan['id']), href='#/capabilities/wellbeing/interventions'))
    data = dict(schema_version=1, rows=rows, limitation=LIMITATION, coverage={'measurement_limit': 500, 'laboratory_limit': 500, 'goal_limit': 200, 'intervention_limit': 200, 'possibly_truncated': len(measures) == 500 or len(labs) == 500 or len(goals) > 200 or len(plans) > 200})
    data['fingerprint'] = hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return data


def view(home=None):
    home = Path(home or config_dir())
    return {**snapshot(home), 'narratives': [dict(slug=row.slug, name=row.name, version=row.version, href=f'#/artifacts/{row.slug}') for row in artifacts(home).list(tag=TAG, kind='json')][:100]}


def read_narrative(slug, version=None, home=None):
    provider = artifacts(home or config_dir())
    row = provider.get(slug, version=version)
    if row is None or TAG not in row.tags or row.kind != 'json':
        raise MeasurementError('Scorecard narrative not found', 404)
    return dict(slug=row.slug, version=version or row.version, versions=provider.list_versions(slug), document=json.loads(row.content))


def save(payload, home=None):
    if not isinstance(payload, dict) or set(payload) - {'fingerprint', 'note', 'slug', 'version'}:
        raise ValueError('Invalid scorecard fields')
    note = payload.get('note', '')
    if not isinstance(note, str) or len(note) > 8000:
        raise ValueError('Narrative note must be at most 8000 characters')
    home = Path(home or config_dir())
    data = snapshot(home)
    if payload.get('fingerprint') != data['fingerprint']:
        raise MeasurementError('Source records changed; refresh the scorecard', 409)
    document = dict(schema_version=1, authored_by='user', note=note, snapshot=data)
    content = json.dumps(document, sort_keys=True, ensure_ascii=False)
    provider = artifacts(home)
    with provider.mutation_lock:
        if payload.get('slug'):
            current = read_narrative(payload['slug'], home=home)
            if type(payload.get('version')) is not int or current['version'] != payload['version']:
                raise MeasurementError('Narrative changed; reload its version', 409)
            row = provider.update(payload['slug'], content=content, snapshot=True, actor='user')
        else:
            slug = 'personal-scorecard-' + hashlib.sha256(content.encode()).hexdigest()[:40]
            existing = provider.get(slug, version=1)
            if existing is not None:
                if existing.content != content or TAG not in existing.tags:
                    raise MeasurementError('Narrative identity conflict', 409)
                return read_narrative(slug, version=1, home=home)
            row = provider.create(name='Personal scorecard', content=content, kind='json', source='manual', slug=slug, tags=[TAG], actor='user')
    return read_narrative(row.slug, home=home)

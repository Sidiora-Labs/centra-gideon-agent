import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path



async def probe(capability, reference, provider, managed=False):
    row = dict(capability=capability, selection=reference, status='unconfigured', available=False,
               inference_verified=False, management_available=managed, model=None, reason='Select a model in Settings')
    if not reference:
        return row
    if provider is None:
        return dict(row, status='provider_missing', reason='The selected provider is not registered')
    if provider.name == 'stub':
        return dict(row, status='preview_only', reason='Offline preview is not a generation readiness check')
    try:
        available = bool(await asyncio.wait_for(provider.is_available(), timeout=5))
        row['available'] = available
        if not available:
            return dict(row, status='unavailable', reason='Provider availability check failed; check its configuration')
        models = await asyncio.wait_for(provider.list_models(), timeout=5)
    except asyncio.TimeoutError:
        return dict(row, status='timeout', reason='Provider readiness check timed out')
    except Exception:
        return dict(row, status='provider_error', reason='Provider readiness check failed')
    return describe_model(row, capability, reference, models)


def describe_model(row, capability, reference, models):
    model_id = reference.split(':', 1)[1] if ':' in reference else ''
    model = next((item for item in models if item.name == model_id), None)
    if model is None:
        return dict(row, status='model_missing', reason='Selected model is absent from the provider catalog')
    metadata = dict(name=model.name, downloaded=bool(model.downloaded))
    if capability == 'image_gen':
        metadata.update(sizes=list(model.sizes), supports_edit=bool(model.supports_edit))
    else:
        metadata.update(aspect_ratios=list(model.aspect_ratios), max_duration_s=model.max_duration_s)
    row['model'] = metadata
    if not model.downloaded:
        return dict(row, status='not_downloaded', reason='Install this model through its existing model provider')
    return dict(row, status='configured', reason='Provider and catalog checks passed; inference has not been verified')


class MediaReadiness:
    def __init__(self, path, bindings=None):
        self.bindings = bindings
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS observation (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT)')
        finally:
            db.close()

    def get(self):
        db = sqlite3.connect(self.path)
        try:
            row = db.execute('SELECT body FROM observation WHERE id=1').fetchone()
        finally:
            db.close()
        return json.loads(row[0]) if row else {'observed_at': None, 'items': [], 'inference_verified': False}

    def selected_bindings(self):
        from gideon.extensions.providers.use_cases import active_model_refs
        from gideon.integrations.image_gen.registry import active_image_gen
        from gideon.integrations.video_gen.registry import active_video_gen
        from gideon.integrations.local_models.registry import get_provider
        bindings = []
        for capability, resolve in [('image_gen', active_image_gen), ('video_gen', active_video_gen)]:
            refs = active_model_refs(capability)
            reference = refs[0] if refs else ''
            selected = resolve()
            bindings.append((capability, reference, selected[0] if selected else None, bool(reference and get_provider(reference.split(':', 1)[0]))))
        return bindings

    async def refresh(self):
        try:
            bindings = self.bindings() if self.bindings else self.selected_bindings()
        except Exception:
            return self.record([dict(capability=capability, selection='', status='provider_error', available=False, inference_verified=False, management_available=False, model=None, reason='Provider discovery failed') for capability in ('image_gen', 'video_gen')])
        rows = await asyncio.gather(*(probe(*binding) for binding in bindings))
        return self.record(rows)

    def record(self, rows):
        result = dict(observed_at=datetime.now(timezone.utc).isoformat(), items=rows, inference_verified=False)
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute('INSERT OR REPLACE INTO observation VALUES (1,?)', (json.dumps(result, allow_nan=False),))
        finally:
            db.close()
        return result

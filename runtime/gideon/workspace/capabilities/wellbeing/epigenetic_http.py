from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from .epigenetic import EpigeneticStore
from .store import MeasurementError


EPIGENETIC_STORE = web.AppKey('wellbeing_epigenetic', EpigeneticStore)


async def handle(request):
    store = request.app[EPIGENETIC_STORE]
    try:
        if request.query.keys() - {'limit', 'offset'}:
            raise MeasurementError('Unknown epigenetic query field')
        identity = request.match_info.get('id')
        if request.method in {'POST', 'PUT'}:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise MeasurementError('Expected JSON object')
            value = store.create(payload) if request.method == 'POST' else store.correct(identity, payload)
            return web.json_response(value, status=201 if request.method == 'POST' else 200)
        if request.path.endswith('/history'):
            return web.json_response({'history': store.history(identity)})
        if request.path.endswith('/export'):
            return web.json_response(store.export())
        if identity:
            return web.json_response(store.get(identity))
        return web.json_response({'records': store.list(int(request.query.get('limit', '100')), int(request.query.get('offset', '0')))})
    except MeasurementError as exc:
        return web.json_response({'error': str(exc), 'code': exc.code}, status=exc.status)
    except (ValueError, TypeError, OverflowError):
        return web.json_response({'error': 'Invalid epigenetic request', 'code': 'invalid_request'}, status=400)


def register(app: web.Application, home: Path | None = None):
    app[EPIGENETIC_STORE] = EpigeneticStore(home if home is not None else config_dir())
    base = '/api/capabilities/wellbeing/epigenetic'
    app.router.add_post(base, handle)
    app.router.add_get(base, handle)
    app.router.add_get(base + '/export', handle)
    app.router.add_get(base + '/{id}/history', handle)
    app.router.add_get(base + '/{id}', handle)
    app.router.add_put(base + '/{id}', handle)

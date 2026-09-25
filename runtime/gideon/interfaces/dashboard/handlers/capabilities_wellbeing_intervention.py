"""Captured-home intervention schedules and recorded adherence."""
import asyncio
from pathlib import Path
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.intervention import InterventionStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = InterventionStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get('id')
            suffix = request.path.rsplit('/', 1)[-1]
            plans = '/plans' in request.path
            if request.method in ('POST', 'PUT'):
                body = await read_json_body(request)
                operation = store.create_plan if not identity else store.record if request.method == 'POST' else store.update_plan if plans else store.correct_record
                result = await asyncio.to_thread(operation, identity, body) if identity else await asyncio.to_thread(operation, body)
            elif suffix == 'summary':
                result = await asyncio.to_thread(store.summary, identity, days=int(request.query.get('days', '30')), as_of=request.query.get('as_of'))
            elif suffix == 'history':
                result = {'history': await asyncio.to_thread(store.history_plan if plans else store.history_record, identity)}
            elif suffix == 'plans':
                archived = request.query.get('include_archived', 'false')
                if archived not in ('true', 'false'):
                    raise MeasurementError('include_archived must be true or false')
                result = {'plans': await asyncio.to_thread(store.list_plans, archived == 'true')}
            elif suffix == 'records':
                result = {'records': await asyncio.to_thread(store.list_records, identity)}
            else:
                result = await asyncio.to_thread(store.get_plan if plans else store.get_record, identity)
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error('invalid_request', message=str(exc), status=400)

    base = '/api/capabilities/wellbeing/interventions'
    for path in ('plans', 'plans/{id}', 'plans/{id}/history', 'plans/{id}/records', 'plans/{id}/summary', 'records/{id}', 'records/{id}/history'):
        app.router.add_get(base + '/' + path, handle)
    app.router.add_post(base + '/plans', handle)
    app.router.add_post(base + '/plans/{id}/records', handle)
    app.router.add_put(base + '/plans/{id}', handle)
    app.router.add_put(base + '/records/{id}', handle)

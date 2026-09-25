"""Captured-home memory practice cards and schedules."""
import asyncio
from pathlib import Path
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.memory_practice import MemoryPracticeStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = MemoryPracticeStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get('id')
            if request.method in ('POST', 'PUT'):
                body = await read_json_body(request)
                method = store.create if not identity else store.update if request.method == 'PUT' else store.practice
                result = await asyncio.to_thread(method, identity, body) if identity else await asyncio.to_thread(method, body)
            elif request.path.endswith('/history'):
                result = {'history': await asyncio.to_thread(store.history, identity)}
            elif identity:
                result = await asyncio.to_thread(store.get, identity)
            else:
                query = request.query
                if query.get('due_only', 'false') not in ('true', 'false') or query.get('include_archived', 'false') not in ('true', 'false'):
                    raise MeasurementError('Filters must be true or false')
                result = {'cards': await asyncio.to_thread(store.list_cards, due_only=query.get('due_only') == 'true', include_archived=query.get('include_archived') == 'true', as_of=query.get('as_of'), limit=int(query.get('limit', '100')))}
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error('invalid_request', message=str(exc), status=400)

    base = '/api/capabilities/wellbeing/memory/cards'
    app.router.add_get(base, handle)
    app.router.add_post(base, handle)
    app.router.add_get(base + '/{id}', handle)
    app.router.add_put(base + '/{id}', handle)
    app.router.add_get(base + '/{id}/history', handle)
    app.router.add_post(base + '/{id}/practice', handle)

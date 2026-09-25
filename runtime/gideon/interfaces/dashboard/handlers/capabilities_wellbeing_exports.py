"""Captured-home versioned wellbeing export endpoints."""
import asyncio
from pathlib import Path
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.exports import ExportStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = ExportStore(home if home is not None else config_dir())

    async def handle(request):
        try:
            identity = request.match_info.get('id')
            if request.method == 'POST':
                result = await asyncio.to_thread(store.create, await read_json_body(request))
            elif request.path.endswith('/preview'):
                result = await asyncio.to_thread(store.preview)
            elif request.path.endswith('/download'):
                raw = await asyncio.to_thread(store.download, identity)
                return web.Response(body=raw, content_type='application/json', headers={'Content-Disposition': f'attachment; filename="wellbeing-{identity}.json"'})
            elif identity:
                result = await asyncio.to_thread(store.get, identity)
            else:
                result = {'exports': await asyncio.to_thread(store.list_exports)}
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error('invalid_request', message=str(exc), status=400)

    base = '/api/capabilities/wellbeing/exports'
    app.router.add_get(base, handle)
    app.router.add_get(base + '/preview', handle)
    app.router.add_post(base, handle)
    app.router.add_get(base + '/{id}', handle)
    app.router.add_get(base + '/{id}/download', handle)

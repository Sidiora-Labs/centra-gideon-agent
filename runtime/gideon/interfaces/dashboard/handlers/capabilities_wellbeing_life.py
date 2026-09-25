"""Life projections and milestones with canonical local inbox reminders."""
import asyncio
from pathlib import Path
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.integrations.inbox import live_store
from gideon.integrations.action_providers.registry import register_action_provider
from gideon.workspace.capabilities.wellbeing.life_calendar import LifeCalendarStore
from gideon.workspace.capabilities.wellbeing.life_provider import WellbeingReminderAction
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    store = LifeCalendarStore(home if home is not None else config_dir())
    register_action_provider(WellbeingReminderAction())

    async def handle(request):
        try:
            identity = request.match_info.get('id')
            path = request.path
            if path.endswith('/reminder/check'):
                if request.can_read_body:
                    body = await read_json_body(request)
                    if body:
                        raise MeasurementError('Reminder check accepts no overrides')
                result = await asyncio.to_thread(store.check_reminder, inbox=live_store(request.app.get('state')))
            elif request.method in ('POST', 'PUT'):
                body = await read_json_body(request)
                method = store.configure if path.endswith('/config') else store.update_event if identity else store.create_event
                result = await asyncio.to_thread(method, identity, body) if identity else await asyncio.to_thread(method, body)
            elif path.endswith('/projection'):
                result = await asyncio.to_thread(store.projection, request.query.get('as_of'))
            elif path.endswith('/history'):
                result = {'history': await asyncio.to_thread(store.history_event, identity) if identity else await asyncio.to_thread(store.config_history)}
            elif path.endswith('/events'):
                result = {'events': await asyncio.to_thread(store.list_events)}
            else:
                result = await asyncio.to_thread(store.get_config)
            return web.json_response(result)
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except (RequestValidationError, ValueError) as exc:
            return json_error('invalid_request', message=str(exc), status=400)

    base = '/api/capabilities/wellbeing/life'
    for path in ('config', 'config/history', 'projection', 'events', 'events/{id}/history'):
        app.router.add_get(base + '/' + path, handle)
    app.router.add_put(base + '/config', handle)
    app.router.add_post(base + '/events', handle)
    app.router.add_put(base + '/events/{id}', handle)
    app.router.add_post(base + '/reminder/check', handle)

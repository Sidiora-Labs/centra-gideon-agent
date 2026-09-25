from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.communications import PeopleStore
from gideon.workspace.capabilities.wellbeing.privacy_broker_beenverified import BeenVerifiedCaseAdapter
from gideon.workspace.capabilities.wellbeing.privacy_brokers import PrivacyBrokerStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app, home=None, adapter=None):
    root = home if home is not None else config_dir()
    service = adapter or BeenVerifiedCaseAdapter(PrivacyBrokerStore(root), PeopleStore())

    async def action(request):
        try:
            value = getattr(service, request.match_info['action'])(request.match_info['id'], await read_json_body(request))
            response = web.json_response(value); response.headers['Cache-Control'] = 'no-store'; return response
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error('invalid_request', message=str(exc), status=400)

    async def status(request):
        try:
            return web.json_response(service.status(request.match_info['id'], int(request.query.get('revision', ''))), headers={'Cache-Control':'no-store'})
        except (ValueError, MeasurementError) as exc:
            if isinstance(exc, MeasurementError): return json_error(exc.code, message=str(exc), status=exc.status)
            return json_error('invalid_request', message='revision is required', status=400)

    app.router.add_get('/api/capabilities/wellbeing/privacy/broker-cases/{id}/providers/beenverified', status)
    app.router.add_post('/api/capabilities/wellbeing/privacy/broker-cases/{id}/providers/beenverified/{action:prepare|approve|send|correlate}', action)

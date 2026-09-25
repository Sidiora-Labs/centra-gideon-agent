"""Explicit owner-triggered Spokeo broker protocol routes."""
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.privacy_broker_spokeo import SpokeoCaseAdapter
from gideon.workspace.capabilities.wellbeing.privacy_brokers import PrivacyBrokerStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None, adapter=None):
    service = adapter or SpokeoCaseAdapter(PrivacyBrokerStore(home if home is not None else config_dir()))

    async def action(request):
        try:
            method = getattr(service, request.match_info['action'])
            value = await method(request.match_info['id'], await read_json_body(request))
            response = web.json_response(value)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error('invalid_request', message=str(exc), status=400)

    app.router.add_post(
        '/api/capabilities/wellbeing/privacy/broker-cases/{id}/providers/spokeo/{action:scan|prepare|submit|verify}',
        action,
    )

"""Owner interface for local encrypted privacy facts and explicit consent."""
import asyncio
from pathlib import Path
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.http_errors import json_error
from gideon.workspace.capabilities.wellbeing.privacy import PrivacyStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def register(app: web.Application, home: Path | None = None):
    bound_home = home if home is not None else config_dir()
    store = PrivacyStore(bound_home)

    async def handle(request):
        try:
            identity = request.match_info.get('id')
            scope, suffix = request.match_info.get('scope'), request.match_info.get('suffix', '')
            if request.method == 'GET':
                if scope == 'subjects':
                    method, envelope = (store.list_subjects, 'subjects') if not identity else (store.consents, 'consents') if suffix == 'consents' else (store.list_facts, 'facts') if suffix == 'facts' else (store.audit, 'audit') if suffix == 'audit' else (store.get_subject, None)
                else:
                    method, envelope = (store.history_fact, 'history') if suffix == 'history' else (store.get_fact, None)
                result = await asyncio.to_thread(method, identity) if identity else await asyncio.to_thread(method)
                if envelope:
                    result = {envelope: result}
            else:
                payload = await read_json_body(request)
                if scope == 'subjects':
                    method = store.create_subject if not identity else store.consent if suffix == 'consents' else store.create_fact
                else:
                    method = store.reveal if suffix == 'reveal' else store.correct_fact
                result = await asyncio.to_thread(method, identity, payload) if identity else await asyncio.to_thread(method, payload)
            response = web.json_response(result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except MeasurementError as exc:
            return json_error(exc.code, message=str(exc), status=exc.status)
        except RequestValidationError as exc:
            return json_error('invalid_request', message=str(exc), status=400)

    base = '/api/capabilities/wellbeing/privacy'
    app.router.add_get(base + '/{scope:subjects}', handle)
    app.router.add_post(base + '/{scope:subjects}', handle)
    app.router.add_get(base + '/{scope:subjects}/{id}', handle)
    app.router.add_get(base + '/{scope:subjects}/{id}/{suffix:consents|facts|audit}', handle)
    app.router.add_post(base + '/{scope:subjects}/{id}/{suffix:consents|facts}', handle)
    app.router.add_get(base + '/{scope:facts}/{id}', handle)
    app.router.add_put(base + '/{scope:facts}/{id}', handle)
    app.router.add_get(base + '/{scope:facts}/{id}/{suffix:history}', handle)
    app.router.add_post(base + '/{scope:facts}/{id}/{suffix:reveal}', handle)
    from .capabilities_wellbeing_holdings import register as register_holdings
    register_holdings(app, bound_home)

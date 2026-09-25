"""Reviewed canonical imports bound to one runtime allocation."""

from aiohttp import web
from gideon.cognition.memory_service import MemoryService
from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers._shared import _blocks_reads_session, _is_restricted_session
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.typed import TypedCapture


async def operation(request):
    try:
        allowed = {'limit', 'offset'} if request.method == 'GET' else set()
        if set(request.query) - allowed or any(len(request.query.getall(key)) != 1 for key in request.query):
            raise CaptureError('Unknown or repeated import query')
        state = request.app['state']
        if _blocks_reads_session(state, request) or (request.path.endswith('/commit') and _is_restricted_session(state, request)):
            raise CaptureError('This session cannot access typed imports', 403)
        service = request.app['capability_typed_capture']
        if request.method == 'GET':
            result = service.list(int(request.query.get('limit', '20')), int(request.query.get('offset', '0')))
        else:
            body = await read_json_body(request)
            result = await service.commit(body) if request.path.endswith('/commit') else service.preview(body)
        return web.json_response(result)
    except (ValueError, TypeError) as exc:
        return web.json_response({'error': str(exc)}, status=getattr(exc, 'status', 400))


def register(app):
    state = app['state']
    memory = state.context_builder.memory if state.context_builder else getattr(state, '_standalone_memory', None)
    archive = getattr(memory, 'vector_store', None)
    app['capability_typed_capture'] = TypedCapture(state.knowledge_store, MemoryService.over_vector_store(archive) if archive is not None else None)
    root = '/api/capabilities/knowledge/types'
    app.router.add_get(root, operation)
    app.router.add_post(root + '/preview', operation)
    app.router.add_post(root + '/commit', operation)

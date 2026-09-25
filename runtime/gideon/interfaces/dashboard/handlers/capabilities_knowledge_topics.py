"""Tracked topic operations and live evidence from application-bound stores."""

from aiohttp import web
from gideon.cognition.memory_service import MemoryService
from gideon.core.http_request import read_json_body
from gideon.interfaces.dashboard.handlers.capabilities_knowledge import _redact
from gideon.interfaces.dashboard.handlers._shared import _blocks_reads_session, _is_restricted_session
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.topics import TrackedTopics


async def operation(request):
    try:
        allowed = {'limit', 'offset'} if request.method == 'GET' else set()
        if set(request.query) - allowed or any(len(request.query.getall(key)) != 1 for key in request.query):
            raise CaptureError('Unknown or repeated topic query')
        state = request.app['state']
        if _blocks_reads_session(state, request) or (request.method != 'GET' and _is_restricted_session(state, request)):
            raise CaptureError('This session cannot access tracked topics', 403)
        service = request.app['capability_tracked_topics']
        if request.method == 'GET':
            paging = {'limit': int(request.query.get('limit', '20')), 'offset': int(request.query.get('offset', '0'))}
            result = service.matches(request.match_info['id'], **paging) if 'id' in request.match_info else service.list(**paging)
        else:
            body = await read_json_body(request)
            result = service.delete(request.match_info['id'], body) if request.method == 'DELETE' else service.save(body)
        if request.method == 'GET' and 'id' in request.match_info:
            result['items'] = [_redact(item) for item in result['items']]
        return web.json_response(result)
    except (ValueError, TypeError) as exc:
        return web.json_response({'error': str(exc)}, status=getattr(exc, 'status', 400))


def register(app):
    state = app['state']
    memory = state.context_builder.memory if state.context_builder else getattr(state, '_standalone_memory', None)
    archive = getattr(memory, 'vector_store', None)
    app['capability_tracked_topics'] = TrackedTopics(state.knowledge_store, MemoryService.over_vector_store(archive) if archive is not None else None)
    root = '/api/capabilities/knowledge/topics'
    app.router.add_get(root, operation)
    app.router.add_post(root, operation)
    app.router.add_delete(root + '/{id}', operation)
    app.router.add_get(root + '/{id}/matches', operation)

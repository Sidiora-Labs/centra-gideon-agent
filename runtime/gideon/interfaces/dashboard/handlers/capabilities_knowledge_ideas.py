"""Reviewed portable idea lists and opt-in owned-vault exchange."""

from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.integrations.action_providers.registry import register_action_provider
from gideon.interfaces.dashboard.handlers._shared import _blocks_reads_session, _is_restricted_session
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_journals import public
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.idea_format import preview
from gideon.workspace.capabilities.knowledge.ideas import IdeaLists
from gideon.workspace.capabilities.knowledge.idea_schedule import IdeaSchedules, IdeaSyncActionProvider


async def operation(request):
    try:
        if request.query:
            raise CaptureError('Idea operations do not accept query overrides')
        state = request.app['state']
        if _blocks_reads_session(state, request) or (request.method != 'GET' and _is_restricted_session(state, request)):
            raise CaptureError('This session cannot access personal idea lists', 403)
        ideas = request.app['capability_idea_lists']
        identity = request.match_info.get('identity')
        operation_name = request.match_info.get('operation', '')
        if request.path.endswith('/preview'):
            body = await read_json_body(request)
            if not isinstance(body, dict) or set(body) != {'content'}:
                raise CaptureError('Idea preview requires content only')
            result = preview(body['content'])
        elif request.path.endswith('/import'):
            result = ideas.import_list(await read_json_body(request))
        elif operation_name == 'sync':
            result = ideas.sync(identity, await read_json_body(request))
        elif operation_name == 'schedule':
            result = request.app['capability_idea_schedules'].save(identity, await read_json_body(request))
        elif operation_name == 'export':
            result = ideas.export(identity)
        else:
            result = ideas.get(identity) if identity else ideas.list()
        return web.json_response(public(result))
    except (ValueError, TypeError) as exc:
        return web.json_response({'error': str(exc)}, status=getattr(exc, 'status', 400))


def register(app):
    ideas = IdeaLists(app['state'].knowledge_store)
    schedules = IdeaSchedules(ideas)
    app['capability_idea_lists'], app['capability_idea_schedules'] = ideas, schedules
    register_action_provider(IdeaSyncActionProvider(schedules))
    root = '/api/capabilities/knowledge/ideas'
    app.router.add_get(root, operation)
    app.router.add_post(root + '/preview', operation)
    app.router.add_post(root + '/import', operation)
    app.router.add_get(root + '/{identity}', operation)
    app.router.add_get(root + '/{identity}/{operation:export}', operation)
    app.router.add_post(root + '/{identity}/{operation:sync|schedule}', operation)

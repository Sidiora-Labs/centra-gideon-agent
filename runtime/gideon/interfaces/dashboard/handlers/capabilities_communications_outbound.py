import asyncio

from aiohttp import web

from gideon.workspace.capabilities.communications import PeopleError, PeopleStore
from gideon.workspace.capabilities.communications.outbound_email import OutboundEmail


def register(app, service=None):
    outbound = service or OutboundEmail(PeopleStore())

    async def handle(request):
        try:
            draft_id = request.match_info.get('draft_id')
            action = request.match_info.get('action')
            if request.method == 'GET':
                return web.json_response({'draft': outbound._get(draft_id)} if draft_id else {'drafts': outbound.list()})
            data = await request.json() if request.can_read_body else {}
            if not draft_id:
                row, created = outbound.draft(data)
                return web.json_response({'draft': row, 'created': created}, status=201 if created else 200)
            if action == 'approve': row = outbound.approve(draft_id, data)
            elif action == 'send': row = await asyncio.to_thread(outbound.send, draft_id, data)
            elif action == 'correlate': row = outbound.correlate(draft_id)
            else: raise PeopleError('Unknown outbound email operation', 404)
            return web.json_response({'draft': row})
        except PeopleError as exc:
            return web.json_response({'error': str(exc)}, status=exc.status)

    base = '/api/capabilities/communications/outbound-email/drafts'
    app.router.add_get(base, handle)
    app.router.add_post(base, handle)
    app.router.add_get(base + '/{draft_id}', handle)
    app.router.add_post(base + '/{draft_id}/{action}', handle)

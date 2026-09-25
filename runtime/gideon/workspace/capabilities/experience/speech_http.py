from aiohttp import web
from gideon.core.http_request import read_json_body
from .speech_owner import SpeechOwner
from .proactive_speech import start_digest
from .narration_http import JOBS
from .store import Conflict

OWNER = web.AppKey('experience_speech_owner', SpeechOwner)


async def handle(request):
    owner = request.app[OWNER]
    try:
        if request.method == 'GET':
            result = {'owner': owner.state()}
        else:
            body = await read_json_body(request)
            if '/proactive-speech' in request.path:
                result = start_digest(request.app[JOBS], owner, body, retry=request.path.endswith("/retry"))
            else:
                result = {'owner': owner.change(request.match_info['action'], body)}
        return web.json_response(result)
    except Conflict as exc:
        return web.json_response({'error': str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)


def register_speech(app, store):
    app[OWNER] = SpeechOwner(store)
    prefix = '/api/capabilities/experience'
    app.router.add_get(prefix+'/speech-owner', handle)
    for action in ('enable','claim','renew','release'):
        async def action_handler(request, name=action):
            request.match_info['action'] = name
            return await handle(request)
        app.router.add_post(prefix+'/speech-owner/'+action, action_handler)
    app.router.add_post(prefix+'/proactive-speech', handle)
    app.router.add_post(prefix+'/proactive-speech/retry', handle)

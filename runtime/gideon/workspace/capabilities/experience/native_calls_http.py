import hashlib
import json
import os
from pathlib import Path

from aiohttp import web
from gideon.core.config.loader import CONFIG_DIR_NAME, logger
from gideon.core.config.locations import configuration_home
from gideon.core.http_request import read_json_body
from .native_calls import NativeCalls, get_native_calls
from .store import Conflict, NotFound


def handoff(request, record, body):
    if not isinstance(body, dict) or set(body) != {'conversation', 'messages', 'request_id'}:
        raise ValueError('Expected conversation, messages and request_id')
    NativeCalls.validate({'command': 'probe', 'request_id': body['request_id']})
    messages = body['messages']
    if not isinstance(messages, list) or not 1 <= len(messages) <= 200:
        raise ValueError('Supply between 1 and 200 transcript messages')
    for message in messages:
        if not isinstance(message, dict) or set(message) != {'role', 'text'} or message['role'] not in ('user', 'assistant') or not isinstance(message['text'], str) or not 1 <= len(message['text'].strip()) <= 10000:
            raise ValueError('Invalid user-supplied transcript message')
    if not isinstance(body['conversation'], str) or not body['conversation']:
        raise ValueError('Select an existing persistent conversation')
    state = request.app.get('state')
    session = state.get_session(body['conversation']) if state else None
    if session is None:
        raise NotFound('Existing conversation is unavailable')
    if session.is_restricted or session._ephemeral or ('dashboard:' + session.key) in state._ephemeral_keys or session.running or not state.conversation_log:
        raise Conflict('Handoff requires an idle persistent conversation')
    if getattr(session, '_app', '') != request.get('app', ''):
        raise NotFound('Conversation belongs to another application')
    from gideon.interfaces.dashboard.chat_persistence import save_session_to_history
    from gideon.interfaces.dashboard.chat_utils import persisted_history_key
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    marker = {'native_call_id': record['id'], 'request_id': body['request_id'], 'content_hash': digest, 'source': 'user_supplied_transcript'}
    key = persisted_history_key(state.conversation_log, session.key)
    prior = [m for m in state.conversation_log.read_messages(key) + session.messages if m.get('meta', {}).get('native_call_id') == record['id'] and m.get('meta', {}).get('request_id') == body['request_id']]
    if prior and any(m.get('meta', {}).get('content_hash') != digest for m in prior):
        raise Conflict('Handoff request content changed')
    if not prior:
        text = 'User-supplied native call transcript. Native observation: ' + record['state'] + '. Audio capture and call completion are not verified.\n\n'
        text += '\n\n'.join(m['role'] + ': ' + m['text'] for m in messages)
        session.append('user', text, meta=marker)
    save_session_to_history(state, session)
    persisted = state.conversation_log.read_messages(key)
    if not any(m.get('meta') == marker for m in persisted):
        raise Conflict('Conversation transcript was not durably saved; retry with the same request')
    return {'conversation': body['conversation'], 'call_id': record['id'], 'source': 'user_supplied_transcript', 'state': 'saved', 'agent_continuation': 'not_started'}


def register_native_calls(app, store):
    calls = get_native_calls(store)
    prefix = '/api/capabilities/experience/native-calls'
    async def handle(request):
        try:
            if request.method == 'GET':
                return web.json_response({'readiness': calls.readiness(), 'requests': calls.list()})
            body = await read_json_body(request)
            if request.match_info.get('id'):
                if configuration_home(os.environ.get('GIDEON_HOME'), Path.home() / CONFIG_DIR_NAME, logger).resolve() != calls.home:
                    raise Conflict('Runtime home changed; conversation handoff refused')
                result = handoff(request, calls.get(request.match_info['id']), body)
            else:
                result = await calls.command(body)
            return web.json_response(result)
        except NotFound as exc:
            return web.json_response({'error': str(exc)}, status=404)
        except Conflict as exc:
            return web.json_response({'error': str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_post(prefix + '/{id}/handoff', handle)

"""Exclusive renewable audible ownership shared by browser speech consumers."""

import json
import time
from uuid import uuid4

from .graph import identifier
from .store import Conflict


class SpeechOwner:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS speech_owner(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT)")
            db.execute("INSERT OR IGNORE INTO speech_owner VALUES(1,?)", (json.dumps({'enabled': False, 'owner': '', 'token': '', 'expires_at': 0}),))

    def state(self):
        with self.store.connection() as db:
            state = json.loads(db.execute('SELECT body FROM speech_owner WHERE id=1').fetchone()[0])
        return {key: value for key, value in state.items() if key != 'token'}

    def change(self, action, body):
        fields = {'enable': {'enabled'}, 'claim': {'owner'}, 'renew': {'owner', 'token'}, 'release': {'owner', 'token'}}
        if action not in fields or not isinstance(body, dict) or set(body) != fields[action]:
            raise ValueError('speech ownership fields do not match action')
        if action == 'enable' and type(body['enabled']) is not bool:
            raise ValueError('enabled must be boolean')
        if action != 'enable':
            identifier(body['owner'])
        with self.store.connection() as db:
            state = json.loads(db.execute('SELECT body FROM speech_owner WHERE id=1').fetchone()[0])
            now = time.time()
            if action == 'enable':
                state['enabled'] = body['enabled']
            elif action == 'claim':
                if state['expires_at'] > now:
                    raise Conflict('another audible owner holds the lease')
                state.update(owner=body['owner'], token=uuid4().hex, expires_at=now+15)
            else:
                if state['owner'] != body['owner'] or state['token'] != body['token'] or state['expires_at'] <= now:
                    raise Conflict('audible ownership expired or changed')
                state.update(expires_at=now+15) if action == 'renew' else state.update(owner='', token='', expires_at=0)
            db.execute('UPDATE speech_owner SET body=? WHERE id=1', (json.dumps(state),))
            return state if action in ("claim", "renew") else {key: value for key, value in state.items() if key != "token"}

    def verify(self, body, proactive=False):
        if not isinstance(body, dict) or set(body) != {'owner','token'}:
            raise ValueError('owner and token required')
        with self.store.connection() as db:
            state = json.loads(db.execute('SELECT body FROM speech_owner WHERE id=1').fetchone()[0])
        if state['owner'] != body['owner'] or state['token'] != body['token'] or state['expires_at'] <= time.time():
            raise Conflict('audible ownership expired or changed')
        if proactive and not state['enabled']:
            raise Conflict('proactive speech is disabled')

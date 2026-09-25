import asyncio
import hashlib
import json
import math
import re
import weakref

import aiohttp
from gideon.sdk.security import PROXY_SIGNATURE_HEADER, sign_proxy_request
from .world_engine import WorldEngine
from .ambient import AmbientDisplay
from .store import Conflict, NotFound

KINDS = ('apps', 'agents', 'work', 'goals', 'schedule', 'health')
ASSET = 'eidoverse/assets/models/crate_large_red.glb'


def identifier(value, limit=64):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,' + str(limit) + '}', value):
        raise ValueError('Invalid world, object or request identifier')
    return value


class Worlds:
    def __init__(self, store):
        self.store, self.engine = store, WorldEngine(store)
        self.lock = asyncio.Lock()
        self.connections = {}

    async def close(self):
        for client, socket, reader in self.connections.values():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await socket.close()
            await client.close()
        self.connections.clear()

    async def call(self, world, body=None):
        identifier(world)
        target, secret = self.engine.target()
        path = '/gideon/worlds/' + world
        raw = json.dumps(body).encode() if body is not None else b''
        method = 'POST' if body is not None else 'GET'
        headers = {PROXY_SIGNATURE_HEADER: sign_proxy_request(secret, method, path, raw), 'Content-Type':'application/json'}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
            async with client.request(method, target + path, data=raw, headers=headers, allow_redirects=False) as response:
                result = await response.json()
                if response.status == 404:
                    raise NotFound(result['error'])
                if response.status != 200:
                    raise Conflict(result.get('error', 'World engine refused operation'))
                return result

    async def open(self, world, body):
        identifier(world)
        if not isinstance(body, dict) or body:
            raise ValueError('Open world accepts an empty object')
        target, secret = self.engine.target()
        async with self.lock:
            prior = self.connections.get(world)
            if prior and not prior[1].closed and not prior[2].done():
                return await self.call(world)
            if prior:
                prior[2].cancel()
                await asyncio.gather(prior[2], return_exceptions=True)
                await prior[0].close()
                del self.connections[world]
            if len(self.connections) >= 16:
                raise Conflict('World presence limit reached; restart the display service to release unused worlds')
            client = aiohttp.ClientSession()
            try:
                socket = await client.ws_connect(target + '/ws', headers={PROXY_SIGNATURE_HEADER:sign_proxy_request(secret, 'GET', '/ws', b'')})
                await socket.send_json({'type':'join','world':world,'id':'gideon','agent':True,'avatar':'eidoverse/assets/vrms/claude.vrm'})
                for _ in range(100):
                    message = await asyncio.wait_for(socket.receive_json(), 10)
                    if message.get('type') == 'error':
                        raise Conflict(message.get('error', 'World join refused'))
                    if message.get('type') == 'snapshot':
                        break
                else:
                    raise Conflict('World did not acknowledge presence')
                async def consume():
                    async for _message in socket:
                        pass
                self.connections[world] = (client, socket, asyncio.create_task(consume()))
                return await self.call(world)
            except BaseException:
                await client.close()
                raise

    async def sources(self):
        self.engine.guard()
        from gideon.extensions.apps.manager import list_apps
        from gideon.core.config.loader import AppConfig
        sources, unavailable = [], []
        cards = (await AmbientDisplay(self.store).snapshot())['cards']
        for kind in ('work','goals','schedule','health'):
            card = cards[kind]
            if card['state'] != 'ready':
                unavailable.append(kind)
                continue
            for row in card.get('rows', [])[:10]:
                sources.append({'kind':kind,'id':row['id'],'title':str(row.get('title') or row.get('kind') or row['id'])[:200], 'status':str(row.get('status', row.get('observed_at', 'enabled' if row.get('enabled') else ''))), 'url':'#/capabilities/' + ('wellbeing' if kind == 'health' else 'identity' if kind == 'goals' else 'workspace')})
        for row in list_apps()[:10]:
            sources.append({'kind':'apps','id':row['name'],'title':row.get('display_name') or row['name'],'status':'enabled' if row.get('enabled') else 'disabled','url':'#/apps/' + row['name']})
        for key in list(AppConfig.load().agents)[:10]:
            sources.append({'kind':'agents','id':key,'title':key,'status':'configured','url':'#/settings'})
        return {'sources':sources, 'unavailable':unavailable + ['peers','memory','operations'], 'limit_per_kind':10}

    async def mutate(self, world, action, body):
        if not isinstance(body, dict):
            raise ValueError('World operation requires an object')
        fields = {'expected_seq','request_id','kinds'} if action == 'project' else {'expected_seq','request_id','operation','id'}
        allowed = fields if action == 'project' else fields | {'position'}
        if not fields <= body.keys() or body.keys() - allowed or type(body['expected_seq']) is not int or body['expected_seq'] < -1:
            raise ValueError('World operation fields are invalid')
        identifier(body['request_id'], 96)
        operations = []
        if action == 'project':
            kinds = body['kinds']
            if not isinstance(kinds, list) or not kinds or any(not isinstance(kind, str) or kind not in KINDS for kind in kinds) or len(set(kinds)) != len(kinds):
                raise ValueError('Choose explicit supported source categories')
            snapshot = await self.call(world)
            rows = [row for row in (await self.sources())['sources'] if row['kind'] in kinds]
            if len(rows) > 20:
                raise ValueError('Select fewer categories; at most 20 sources per projection')
            for index, row in enumerate(rows):
                key = 'source_' + hashlib.sha256((row['kind'] + ':' + row['id']).encode()).hexdigest()[:24]
                if key not in snapshot['state']['entities']:
                    operations.append({'verb':'spawn','args':{'id':key,'lib':ASSET,'pos':[index * 2,0,0],'yaw':0}})
                operations.append({'verb':'comp','args':{'id':key,'type':'gideon_source','data':row}})
        else:
            key = identifier(body['id'])
            operation = body['operation']
            if not isinstance(operation, str) or operation not in ('spawn','place','remove'):
                raise ValueError('Unknown object operation')
            args = {'id':key}
            if operation != 'remove':
                position = body.get('position')
                if not isinstance(position, list) or len(position) != 3 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x) > 10000 for x in position):
                    raise ValueError('Position requires three finite bounded coordinates')
                args['pos'] = position
            if operation == 'spawn':
                args.update(lib=ASSET,yaw=0)
            operations.append({'verb':operation,'args':args})
        return await self.call(world, {'expected_seq':body['expected_seq'],'request_id':body['request_id'],'operations':operations,'intent':body})


_instances = weakref.WeakValueDictionary()


def get_worlds(store):
    key = str(store.path.resolve())
    service = _instances.get(key)
    if service is None:
        service = Worlds(store)
        _instances[key] = service
    return service

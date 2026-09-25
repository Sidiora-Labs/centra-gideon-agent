"""Explicitly armed inference listener over an existing direct model provider."""
import asyncio
import hmac
import ipaddress
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from aiohttp import web
from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import config_dir
from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.registry import get_default_registry
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.anthropic import AnthropicProvider
from gideon.operations.usage_ledger import record_from_event

_hosts = {}


class HostError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class InferenceHost:
    def __init__(self, home):
        self.home = Path(home)
        self.path = self.home / 'capabilities/platform/inference_host.json'
        self.runner = None
        self.port = None
        self.active = set()
        self.lock = asyncio.Lock()

    def state(self):
        return json.loads(self.path.read_text()) if self.path.exists() else dict(version=1, revision=0, enabled=False, provider='', bind='127.0.0.1', port=0, capacity=1, peers={}, usage=[])

    def persist(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(state))

    def view(self):
        state = self.state()
        return {**state, 'listening': self.runner is not None, 'actual_port': self.port, 'active': len(self.active), 'queued': 0, 'runtime_lifecycle': 'Existing configured provider; automatic hardware installation and upstream startup are unavailable', 'credentials': [row.name for row in CredentialStore(self.home).list()], 'providers': [row.name for row in get_default_registry().list_entries()]}

    async def configure(self, body):
        if not isinstance(body, dict) or set(body) != {'revision', 'provider', 'bind', 'port', 'capacity', 'peers'}:
            raise HostError('Exact listener configuration required')
        async with self.lock:
            state = self.state()
            if type(body['revision']) is not int or body['revision'] != state['revision']:
                raise HostError('Listener configuration changed', 409)
            if self.runner or state['enabled']:
                raise HostError('Disarm the listener before reconfiguration', 409)
            address = ipaddress.ip_address(body['bind'])
            if address.is_multicast or address.is_unspecified or not (address.is_private or address.is_loopback):
                raise HostError('Use an explicit local/private bind address')
            if type(body['port']) is not int or not 0 <= body['port'] <= 65535 or type(body['capacity']) is not int or not 1 <= body['capacity'] <= 8:
                raise HostError('Invalid port or admission capacity')
            get_default_registry().get_entry(body['provider'])
            peers = body['peers']
            if not isinstance(peers, dict) or not 1 <= len(peers) <= 100 or any(not isinstance(key, str) or not 1 <= len(key) <= 100 or not isinstance(value, str) or not 1 <= len(value) <= 100 for key, value in peers.items()):
                raise HostError('Named peer credential references required')
            store = CredentialStore(self.home)
            if any(not store.has(ref) or len(store.resolve(ref).secret or '') < 24 for ref in peers.values()):
                raise HostError('Peer credentials must resolve to at least 24 characters')
            self.persist({**state, **body, 'revision': state['revision'] + 1})
            return self.view()

    async def start(self):
        async with self.lock:
            if self.runner:
                return self.view()
            state = self.state()
            if not state['provider'] or not state['peers']:
                raise HostError('Configure a provider and peer keys first')
            app = web.Application(client_max_size=65536)
            app.router.add_get('/v1/models', self.models)
            app.router.add_post('/v1/chat/completions', self.complete)
            runner = web.AppRunner(app, shutdown_timeout=5, handler_cancellation=True)
            await runner.setup()
            try:
                await web.TCPSite(runner, state['bind'], state['port']).start()
            except BaseException:
                await runner.cleanup()
                raise
            self.runner, self.port = runner, runner.addresses[0][1]
            self.persist({**state, 'enabled': True, 'revision': state['revision'] + 1})
            return self.view()

    async def stop(self, disarm=True):
        async with self.lock:
            state = self.state()
            if disarm:
                self.persist({**state, 'enabled': False, 'revision': state['revision'] + int(state['enabled'])})
            runner, self.runner, self.port = self.runner, None, None
            tasks = list(self.active)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if runner:
                await runner.cleanup()
            return self.view()

    def peer(self, request):
        if not self.runner or not self.state()['enabled']:
            raise web.HTTPServiceUnavailable(text='Inference listener disabled')
        token = request.headers.get('Authorization', '')
        if not token.startswith('Bearer '):
            raise web.HTTPUnauthorized(text='Peer authentication required')
        store = CredentialStore(self.home)
        for peer, ref in self.state()['peers'].items():
            if store.has(ref) and hmac.compare_digest(token[7:], store.resolve(ref).secret or '') and len(token[7:]) >= 24:
                return peer
        raise web.HTTPUnauthorized(text='Peer authentication unavailable')

    async def models(self, request):
        self.peer(request)
        entry = get_default_registry().get_entry(self.state()['provider'])
        return web.json_response({'object': 'list', 'data': [{'id': entry.model, 'object': 'model', 'owned_by': 'configured-provider', 'availability': 'not_probed'}]})

    async def complete(self, request):
        peer = self.peer(request)
        state = self.state()
        body = await request.json()
        entry = get_default_registry().get_entry(state['provider'])
        if not isinstance(body, dict) or set(body) - {'model', 'messages', 'stream'} or body.get('model') != entry.model or body.get('stream', False) is not False:
            raise web.HTTPBadRequest(text='Exact configured model and non-streaming request required')
        messages = body.get('messages')
        if not isinstance(messages, list) or not 1 <= len(messages) <= 100 or any(not isinstance(row, dict) or set(row) != {'role', 'content'} or row['role'] not in {'system', 'user', 'assistant'} or not isinstance(row['content'], str) for row in messages):
            raise web.HTTPBadRequest(text='Bounded text messages required')
        if not self.runner or not self.state()['enabled']:
            raise web.HTTPServiceUnavailable(text='Inference listener disabled')
        if len(self.active) >= state['capacity']:
            raise web.HTTPTooManyRequests(text='Inference admission full', headers={'Retry-After': '1'})
        task = asyncio.current_task()
        self.active.add(task)
        provider, complete, parts, status = None, None, [], 'failed'
        started = time.monotonic()
        try:
            provider = get_default_registry().build(state['provider'])
            if type(provider) not in {OpenAIProvider, AnthropicProvider}:
                raise HostError('Direct tool-free API provider required')
            await provider.start()
            async with asyncio.timeout(120):
                async for event in provider.complete(messages, tools=[]):
                    if event.kind.startswith('tool') or event.kind == 'permission_request':
                        raise HostError('Tool events are not admitted')
                    if event.kind == 'text_chunk':
                        parts.append(event.text or '')
                        if sum(map(len, parts)) > 1048576:
                            raise HostError('Inference output exceeded limit')
                    if event.kind == 'complete':
                        complete = event
            if complete is None:
                raise HostError('Provider did not return completion evidence')
            record_from_event(complete, source='inference-host', session_key='peer:' + peer, provider=state['provider'], model=entry.model, estimate_if_missing=False)
            status = 'completed'
            return web.json_response({'id': 'chatcmpl-' + uuid.uuid4().hex, 'object': 'chat.completion', 'model': entry.model, 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': ''.join(parts)}, 'finish_reason': complete.stop_reason or 'stop'}], 'usage': {'prompt_tokens': complete.input_tokens, 'completion_tokens': complete.output_tokens, 'total_tokens': complete.input_tokens + complete.output_tokens}})
        except asyncio.CancelledError:
            status = 'cancelled'
            raise
        except Exception:
            raise web.HTTPServiceUnavailable(text='Configured inference provider unavailable')
        finally:
            self.active.discard(task)
            if provider is not None:
                await provider.shutdown()
            current = self.state()
            current['usage'] = (current['usage'] + [dict(id=uuid.uuid4().hex, peer=peer, provider=state['provider'], model=entry.model, status=status, ts=datetime.now(timezone.utc).isoformat(), duration_ms=round((time.monotonic()-started)*1000), input_tokens=complete.input_tokens if complete else None, output_tokens=complete.output_tokens if complete else None)])[-1000:]
            self.persist(current)


def current():
    home = config_dir()
    return _hosts.setdefault(str(home.resolve()), InferenceHost(home))

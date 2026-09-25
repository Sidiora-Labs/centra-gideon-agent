import asyncio
import re

import aiohttp
from aiohttp import web
from yarl import URL
from gideon.core.http_request import read_json_body
from gideon.sdk.security import PROXY_SIGNATURE_HEADER, sign_proxy_request
from .world_engine import WorldEngine, PREFIX
from .store import Conflict


def rewrite_text(text, content_type):
    base = PREFIX + '/host'
    text = re.sub(r'''(["'`])/(?!/)([^"'`\s<>]*)''', lambda match: match[1] + base + '/' + match[2], text)
    if 'html' in content_type:
        script = '<base href="' + base + '/"><script>const bridgeBase=' + repr(base) + ';const bridgeURL=u=>{const v=new URL(u,location.href);if(v.host===location.host&&!v.pathname.startsWith(bridgeBase+"/"))v.pathname=bridgeBase+v.pathname;return v.href};const nativeFetch=window.fetch;window.fetch=(u,o)=>nativeFetch(u instanceof Request?new Request(bridgeURL(u.url),u):bridgeURL(u),o);const NativeSocket=window.WebSocket;window.WebSocket=class extends NativeSocket{constructor(u,p){super(bridgeURL(u),p)}};</script>'
        text = text.replace('<head>', '<head>' + script, 1)
    return text


def register_world_engine(app, store):
    engine = WorldEngine(store)
    async def control(request):
        try:
            result = await engine.status() if request.method == 'GET' else await engine.control(request.path.rsplit('/', 1)[-1], await read_json_body(request))
            return web.json_response(result)
        except Conflict as exc:
            return web.json_response({'error': str(exc)}, status=409)
        except (ValueError, OSError) as exc:
            return web.json_response({'error': str(exc)}, status=400)

    async def proxy(request):
        try:
            target, secret = engine.target()
            tail = request.match_info.get('tail', '')
            if any(part in ('.', '..') for part in tail.split('/')) or '\\' in tail:
                raise ValueError('Invalid world resource path')
            url = URL(target).with_path('/' + tail).with_query(request.rel_url.query)
            body = await request.read()
            headers = {key: request.headers[key] for key in ('Accept', 'Content-Type', 'Range', 'If-None-Match') if key in request.headers}
            headers[PROXY_SIGNATURE_HEADER] = sign_proxy_request(secret, request.method, url.raw_path_qs, body)
            trace = aiohttp.TraceConfig()
            async def refuse_redirect(*args):
                raise ValueError('World bridge redirects are refused')
            trace.on_request_redirect.append(refuse_redirect)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120), trace_configs=[trace]) as client:
                if request.headers.get('Upgrade', '').lower() == 'websocket':
                    async with client.ws_connect(url, headers=headers, max_msg_size=16 * 1024 * 1024) as upstream:
                        downstream = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
                        await downstream.prepare(request)
                        async def relay(source, destination):
                            async for message in source:
                                if message.type == aiohttp.WSMsgType.TEXT:
                                    await destination.send_str(message.data)
                                elif message.type == aiohttp.WSMsgType.BINARY:
                                    await destination.send_bytes(message.data)
                                else:
                                    break
                        tasks = [asyncio.create_task(relay(upstream, downstream)), asyncio.create_task(relay(downstream, upstream))]
                        try:
                            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            await downstream.close()
                        return downstream
                async with client.request(request.method, url, data=body, headers=headers, allow_redirects=False) as response:
                    raw = await response.content.read(128 * 1024 * 1024 + 1)
                    if len(raw) > 128 * 1024 * 1024:
                        raise ValueError('World resource exceeds bridge limit')
                    content_type = response.headers.get('Content-Type', 'application/octet-stream')
                    if any(kind in content_type for kind in ('html', 'javascript', 'text/css')):
                        raw = rewrite_text(raw.decode('utf-8'), content_type).encode('utf-8')
                    returned = {'Content-Type': content_type, 'Cache-Control': 'no-store'}
                    if 'Content-Range' in response.headers:
                        returned['Content-Range'] = response.headers['Content-Range']
                    return web.Response(body=raw, status=response.status, headers=returned)
        except Conflict as exc:
            return web.json_response({'error': str(exc)}, status=409)
        except (ValueError, UnicodeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return web.json_response({'error': 'Managed world engine connection failed: ' + str(exc)}, status=502)
    app.router.add_get(PREFIX, control)
    app.router.add_post(PREFIX + '/start', control)
    app.router.add_post(PREFIX + '/stop', control)
    app.router.add_route('*', PREFIX + '/host/{tail:.*}', proxy)

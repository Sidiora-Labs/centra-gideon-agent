import asyncio
import json
import sqlite3

import aiohttp
from aiohttp import web
from yarl import URL

from gideon.core.http_request import read_json_body
from gideon.sdk.security import PROXY_SIGNATURE_HEADER, sign_proxy_request
from .store import Conflict, NotFound
from .world_travel import WorldTravel
from .worlds import get_worlds
from .world_engine import WorldEngine
from .world_engine_http import rewrite_text


def register_world_travel(app, store, peers=None):
    if peers is None:
        from gideon.workspace.capabilities.platform.peers import PeerStore
        peers = PeerStore(store.path.parent.parent)
    engine = WorldEngine(store)
    async def guest_proxy(request, travel):
        guest = travel.guest(request.match_info["ticket"])
        if request.method not in ("GET", "HEAD"):
            return web.json_response({"error":"Guest world bridge is read-only"}, status=405)
        target, secret = engine.target()
        tail = request.match_info.get("tail", "")
        if any(part in (".", "..") for part in tail.split("/")) or "\\" in tail:
            raise ValueError("Invalid world resource path")
        query = request.rel_url.query.copy()
        if query.get("world", guest["world"]) != guest["world"]:
            raise Conflict("Guest invitation is scoped to another world")
        query["world"] = guest["world"]
        url = URL(target).with_path("/" + tail).with_query(query)
        body = await request.read()
        headers = {key:request.headers[key] for key in ("Accept", "Content-Type", "Range", "If-None-Match") if key in request.headers}
        headers[PROXY_SIGNATURE_HEADER] = sign_proxy_request(secret, request.method, url.raw_path_qs, body)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as client:
            if request.headers.get("Upgrade", "").lower() == "websocket":
                async with client.ws_connect(url, headers=headers, max_msg_size=16 * 1024 * 1024) as upstream:
                    downstream = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
                    await downstream.prepare(request)
                    async def up():
                        async for message in upstream:
                            if message.type == aiohttp.WSMsgType.TEXT: await downstream.send_str(message.data)
                            elif message.type == aiohttp.WSMsgType.BINARY: await downstream.send_bytes(message.data)
                    async def down():
                        joined = False
                        async for message in downstream:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                value = json.loads(message.data)
                                if value.get("type") == "join":
                                    if joined: raise ValueError("Guest may join only once")
                                    joined = True
                                    value.update(world=guest["world"], id="guest_" + guest["visit_id"], agent=False)
                                elif value.get("type") not in ("pose", "history"):
                                    await downstream.send_json({"type":"error", "error":"Guest world access is limited to presence and history"})
                                    continue
                                await upstream.send_json(value)
                            elif message.type == aiohttp.WSMsgType.BINARY: await upstream.send_bytes(message.data)
                    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
                    try: await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks: task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        await downstream.close()
                    return downstream
            async with client.request(request.method, url, data=body, headers=headers, allow_redirects=False) as response:
                raw = await response.content.read(128 * 1024 * 1024 + 1)
                if len(raw) > 128 * 1024 * 1024: raise ValueError("World resource exceeds bridge limit")
                content_type = response.headers.get("Content-Type", "application/octet-stream")
                base = "/api/capabilities/experience/world-travel/guest/" + request.match_info["ticket"] + "/host"
                if any(kind in content_type for kind in ("html", "javascript", "text/css")):
                    raw = rewrite_text(raw.decode(), content_type, base).encode()
                return web.Response(body=raw, status=response.status, headers={"Content-Type":content_type, "Cache-Control":"no-store"})
    async def handle(request):
        try:
            travel = WorldTravel(store, peers, get_worlds(store))
            action = request.match_info.get("action")
            if request.match_info.get("ticket") is not None:
                action = "leave" if request.path.endswith("/leave") else "guest"
            else:
                action = "admit" if request.path.endswith("/federation/admit") else "depart" if request.path.endswith("/depart") else action
            if request.match_info.get("tail") is not None:
                return await guest_proxy(request, travel)
            if request.method == "GET":
                result = travel.guest(request.match_info["ticket"]) if action == "guest" else await travel.destinations()
            else:
                body = await read_json_body(request)
                if action == "depart":
                    result = await travel.depart(body)
                elif action == "admit":
                    if set(body) != {"proof", "payload"} or not isinstance(body["proof"], dict) or not isinstance(body["payload"], dict):
                        raise ValueError("Signed peer envelope requires proof and payload")
                    peer = await asyncio.to_thread(peers.verify_proof, body["proof"])
                    result = await travel.admit(peer["id"], body["payload"])
                else:
                    result = travel.leave(request.match_info["ticket"], body)
            return web.json_response(result)
        except NotFound as exc:
            return web.json_response({"error":str(exc)}, status=404)
        except Conflict as exc:
            return web.json_response({"error":str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error":str(exc)}, status=getattr(exc, "status", 400))
        except (aiohttp.ClientError, asyncio.TimeoutError, sqlite3.Error):
            return web.json_response({"error":"Canonical peer travel failed"}, status=502)
    prefix = "/api/capabilities/experience/world-travel"
    app.router.add_get(prefix + "/destinations", handle)
    app.router.add_post(prefix + "/depart", handle, name="experience-world-travel-depart")
    app.router.add_post(prefix + "/federation/admit", handle, name="experience-world-travel-admit")
    app.router.add_get(prefix + "/guest/{ticket}", handle, name="experience-world-travel-guest")
    app.router.add_post(prefix + "/guest/{ticket}/leave", handle, name="experience-world-travel-leave")
    app.router.add_route("*", prefix + "/guest/{ticket}/host/{tail:.*}", handle, name="experience-world-travel-host")

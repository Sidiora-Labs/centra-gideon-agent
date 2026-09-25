"""Home-bound monophonic transcription and MIDI editing routes."""

import asyncio

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.catalog import MusicCatalog
from gideon.workspace.capabilities.music.midi import MidiStore
from gideon.workspace.capabilities.music.store import DomainError


def register(app, store=None):
    home = config_dir()
    store = store or MidiStore(
        home / "capabilities" / "music",
        MusicCatalog(
            home / "capabilities" / "music",
            NativeArtifactProvider(root=home / "artifacts"),
        ),
    )

    async def handle(request):
        try:
            item_id = request.match_info.get("id")
            if request.path.endswith("/export"):
                result = await asyncio.to_thread(
                    store.export, item_id, await read_json_body(request)
                )
                return web.json_response(result)
            if request.method == "GET":
                result = (
                    await asyncio.to_thread(store.get, item_id)
                    if item_id
                    else await asyncio.to_thread(
                        store.list,
                        int(request.query.get("offset", 0)),
                        int(request.query.get("limit", 50)),
                    )
                )
                return web.json_response({"item" if item_id else "items": result})
            data = await read_json_body(request)
            result = (
                await asyncio.to_thread(store.update, item_id, data)
                if item_id
                else await asyncio.to_thread(store.transcribe, data)
            )
            return web.json_response({"item": result}, status=200 if item_id else 201)
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response(
                {"error": getattr(exc, "code", "invalid_input"), "message": str(exc)},
                status=getattr(exc, "status", 400),
            )

    prefix = "/api/capabilities/music/midi"
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_get(prefix + "/{id}", handle)
    app.router.add_patch(prefix + "/{id}", handle)
    app.router.add_post(prefix + "/{id}/export", handle)

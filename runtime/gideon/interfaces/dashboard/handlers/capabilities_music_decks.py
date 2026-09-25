"""Card deck editing and canonical print exports."""

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.media.sketches import SketchError
from gideon.workspace.capabilities.music.decks import DeckStore
from gideon.workspace.capabilities.music.store import DomainError


def register(app, store=None, assist=None):
    home = config_dir()
    store = store or DeckStore(home, NativeArtifactProvider(root=home / "artifacts"))

    async def handle(request):
        try:
            info = request.match_info
            item_id = info.get("id")
            key = info.get("key")
            action = request.path.rsplit("/", 1)[-1]
            if "slug" in info:
                version = int(info["version"])
                artifact = store.artifacts.get(info["slug"], version=version)
                if not artifact:
                    raise DomainError("Artifact unavailable", 404)
                raw = store.artifacts.raw_bytes(info["slug"], version=version)
                if raw:
                    return web.Response(body=raw[0], content_type=raw[1])
                return web.Response(
                    text=artifact.content, content_type="application/json"
                )
            if request.method == "GET":
                return web.json_response(
                    {"items": store.history(item_id)}
                    if action == "history"
                    else (
                        {"item": store.get(item_id)}
                        if item_id
                        else {"items": store.list()}
                    )
                )
            body = await read_json_body(request)
            if action == "export":
                return web.json_response(store.export(item_id, body))
            if action == "generate":
                return web.json_response(store.generate(item_id, body))
            if action == "adopt":
                return web.json_response({"item": store.adopt(item_id, key, body)})
            item = (
                store.card(item_id, key, body)
                if key
                else store.update(item_id, body) if item_id else store.create(body)
            )
            return web.json_response({"item": item})
        except (DomainError, SketchError, ValueError, TypeError) as exc:
            return web.json_response(
                {"error": getattr(exc, "code", "invalid_input"), "message": str(exc)},
                status=getattr(exc, "status", 400),
            )

    base = "/api/capabilities/music/decks"
    app.router.add_get(base, handle)
    app.router.add_post(base, handle)
    app.router.add_get(base + "/artifacts/{slug}/{version}/raw", handle)
    app.router.add_get(base + "/{id}", handle)
    app.router.add_patch(base + "/{id}", handle)
    app.router.add_get(base + "/{id}/history", handle)
    app.router.add_patch(base + "/{id}/cards/{key}", handle)
    for action in ("export", "generate"):
        app.router.add_post(base + "/{id}/" + action, handle)
    app.router.add_post(base + "/{id}/cards/{key}/adopt", handle)

    from gideon.workspace.capabilities.music.deck_assist import DeckAssist

    from .capabilities_music_deck_assist import register as register_assist

    register_assist(app, assist or DeckAssist(store))

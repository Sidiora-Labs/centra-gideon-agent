"""Canonical listening imports and named Spotify connection reads."""

from aiohttp import web

from gideon.core.config.loader import config_dir
from gideon.core.http_request import read_json_body
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.listening import ListeningStore
from gideon.workspace.capabilities.music.spotify import SpotifyBridge
from gideon.workspace.capabilities.music.spotify_oauth import SpotifyOAuth
from gideon.workspace.capabilities.music.store import DomainError


def register(app, store=None, bridge=None):
    home = config_dir()
    store = store or ListeningStore(
        home, NativeArtifactProvider(root=home / "artifacts")
    )
    bridge = bridge or SpotifyBridge(home, store)

    async def handle(request):
        try:
            path = request.path
            if "/spotify/" in path:
                if path.endswith("/authorize"):
                    return web.json_response(
                        SpotifyOAuth(bridge).begin(await read_json_body(request))
                    )
                if path.endswith("/complete"):
                    return web.json_response(
                        await SpotifyOAuth(bridge).complete(
                            await read_json_body(request)
                        )
                    )
                if path.endswith("/config"):
                    return web.json_response(
                        {
                            "config": (
                                bridge.config()
                                if request.method == "GET"
                                else bridge.configure(await read_json_body(request))
                            )
                        }
                    )
                if path.endswith("/readiness"):
                    return web.json_response(bridge.readiness())
                return web.json_response(
                    await bridge.sync(await read_json_body(request))
                )
            if path.endswith("/imports"):
                return web.json_response(
                    {"items": store.imports()}
                    if request.method == "GET"
                    else store.import_data(await read_json_body(request))
                )
            if path.endswith("/stats"):
                return web.json_response(store.stats())
            if "/playlists" in path:
                return web.json_response(
                    {"item": store.playlist(request.match_info["id"])}
                    if "id" in request.match_info
                    else {"items": store.playlists()}
                )
            query = dict(request.query)
            for key in ("offset", "limit"):
                if key in query:
                    query[key] = int(query[key])
            return web.json_response(store.history(query))
        except (DomainError, ValueError, TypeError) as exc:
            return web.json_response(
                {"error": getattr(exc, "code", "invalid_input"), "message": str(exc)},
                status=getattr(exc, "status", 400),
            )

    prefix = "/api/capabilities/music/listening"
    for suffix in (
        "history",
        "stats",
        "imports",
        "playlists",
        "playlists/{id}",
        "spotify/config",
        "spotify/readiness",
    ):
        app.router.add_get(prefix + "/" + suffix, handle)
    app.router.add_post(prefix + "/imports", handle)
    app.router.add_post(prefix + "/spotify/sync", handle)
    app.router.add_patch(prefix + "/spotify/config", handle)
    app.router.add_post(prefix + "/spotify/authorize", handle)
    app.router.add_post(prefix + "/spotify/complete", handle)

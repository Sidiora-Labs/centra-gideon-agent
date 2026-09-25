"""Review and apply grounded model proposals for card decks."""

from aiohttp import web

from gideon.core.http_request import read_json_body
from gideon.workspace.capabilities.creative.store import CatalogError
from gideon.workspace.capabilities.music.store import DomainError


def register(app, assist):
    async def handle(request):
        try:
            deck_id = request.match_info.get("id")
            proposal_id = request.match_info.get("proposal_id")
            action = request.path.rsplit("/", 1)[-1]
            if request.method == "GET":
                if action == "providers":
                    return web.json_response({"items": assist.providers()})
                return web.json_response(
                    {"item": assist.get(deck_id, proposal_id)}
                    if proposal_id
                    else {"items": assist.list(deck_id)}
                )
            body = await read_json_body(request)
            if action == "apply":
                return web.json_response(assist.apply(deck_id, proposal_id, body))
            return web.json_response(
                {"item": await assist.propose(deck_id, action, body)}
            )
        except (DomainError, CatalogError, ValueError, TypeError) as exc:
            return web.json_response(
                {"error": getattr(exc, "code", "invalid_input"), "message": str(exc)},
                status=getattr(exc, "status", 400),
            )

    base = "/api/capabilities/music/decks"
    app.router.add_get(base + "/assist/providers", handle)
    app.router.add_get(base + "/{id}/assist", handle)
    app.router.add_get(base + "/{id}/assist/{proposal_id}", handle)
    for kind in ("analyze", "prompts"):
        app.router.add_post(base + "/{id}/assist/" + kind, handle)
    app.router.add_post(base + "/{id}/assist/{proposal_id}/apply", handle)

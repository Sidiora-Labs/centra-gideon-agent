from aiohttp import web

from gideon.core.http_request import read_json_body
from .navigation import NavigationReceipts
from .store import Conflict, NotFound

RECEIPTS = web.AppKey("experience_navigation", NavigationReceipts)


async def handle(request):
    receipts = request.app[RECEIPTS]
    key = request.match_info.get("receipt_id")
    try:
        if request.method == "GET":
            result = {"receipt": receipts.get(key)} if key else {"receipts": receipts.list()}
        else:
            body = await read_json_body(request)
            result = {"receipt": receipts.acknowledge(key, body) if key else receipts.request(body)}
        return web.json_response(result, status=201 if request.method == "POST" and not key else 200)
    except NotFound as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except Conflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


def register_navigation(app, store):
    app[RECEIPTS] = NavigationReceipts(store)
    prefix = "/api/capabilities/experience/navigation"
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix, handle)
    app.router.add_get(prefix + "/{receipt_id}", handle)
    app.router.add_post(prefix + "/{receipt_id}/acknowledge", handle)

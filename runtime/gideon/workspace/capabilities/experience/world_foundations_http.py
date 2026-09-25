import aiohttp
from aiohttp import web

from gideon.core.http_request import read_json_body

from .store import Conflict, NotFound
from .world_foundations import WorldFoundations


def register_world_foundations(app, store):
    service = WorldFoundations(store)

    async def handle(request):
        try:
            key = request.match_info.get("id")
            action = (
                request.match_info.get("action")
                or request.path.rstrip("/").rsplit("/", 1)[-1]
            )
            if request.method == "GET":
                result = (
                    service.envelope(key) if action == "envelope" else service.list()
                )
            else:
                body = await read_json_body(request)
                if action == "record":
                    result = {"foundation": service.record(body)}
                elif action == "inherit":
                    result = {"foundation": service.inherit(body)}
                elif action == "withdrawal":
                    result = {"foundation": service.apply_withdrawal(body)}
                elif action in ("package", "promote", "adopt"):
                    if not isinstance(body, dict) or set(body) != {"revision"}:
                        raise ValueError("Expected revision only")
                    result = {
                        "foundation": getattr(service, action)(key, body["revision"])
                    }
                elif action == "withdraw":
                    if not isinstance(body, dict) or set(body) != {"revision"}:
                        raise ValueError("Expected revision only")
                    result = {"withdrawal": service.withdraw(key, body["revision"])}
                elif action == "install":
                    result = {"controller": await service.install_controller(body)}
                elif action == "reconcile":
                    if body != {}:
                        raise ValueError("Reconcile accepts an empty object")
                    result = await service.reconcile()
                else:
                    if not isinstance(body, dict) or set(body) != {"revision"}:
                        raise ValueError("Expected revision only")
                    result = {
                        "controller": await service.control(
                            key, action, body["revision"]
                        )
                    }
            return web.json_response(result)
        except NotFound as exc:
            return web.json_response({"error": str(exc)}, status=404)
        except Conflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except (aiohttp.ClientError, TimeoutError) as exc:
            return web.json_response(
                {"error": "World engine unavailable: " + str(exc)}, status=502
            )

    prefix = "/api/capabilities/experience/world-foundations"
    app.router.add_get(prefix, handle)
    app.router.add_post(prefix + "/record", handle, name="experience-foundation-record")
    app.router.add_post(
        prefix + "/inherit", handle, name="experience-foundation-inherit"
    )
    app.router.add_post(
        prefix + "/withdrawal", handle, name="experience-foundation-withdrawal"
    )
    app.router.add_post(
        prefix + "/controllers/install", handle, name="experience-controller-install"
    )
    app.router.add_post(
        prefix + "/controllers/reconcile",
        handle,
        name="experience-controller-reconcile",
    )
    for action in ("package", "promote", "adopt", "withdraw"):
        app.router.add_post(
            prefix + "/{id}/" + action, handle, name="experience-foundation-" + action
        )
    app.router.add_get(
        prefix + "/{id}/envelope", handle, name="experience-foundation-envelope"
    )
    for action in ("arm", "restart", "stop", "retire"):
        app.router.add_post(
            prefix + "/controllers/{id}/" + action,
            handle,
            name="experience-controller-" + action,
        )

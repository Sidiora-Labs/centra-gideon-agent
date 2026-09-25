from aiohttp import web

from gideon.integrations.llm.registry import ProviderResolutionError
from gideon.workspace.capabilities.platform.inference_host import HostError, current


async def endpoint(request):
    if not request.get("user") or request.get("app"):
        raise web.HTTPForbidden(text="Dashboard authentication required")
    host = current()
    try:
        if request.method == "GET":
            value = host.view()
        else:
            body = await request.json()
            action = body.get("action")
            value = (
                await host.configure(body["config"])
                if action == "configure"
                else (
                    await host.provision()
                    if action == "provision"
                    else (
                        await host.start()
                        if action == "start"
                        else (
                            await host.readiness()
                            if action == "readiness"
                            else await host.stop() if action == "stop" else None
                        )
                    )
                )
            )
            if value is None:
                raise HostError("Unknown inference host action")
        return web.json_response(value, headers={"Cache-Control": "no-store"})
    except HostError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    except (ValueError, KeyError, TypeError, OSError, ProviderResolutionError):
        return web.json_response(
            {"error": "Inference host unavailable or invalid configuration"}, status=400
        )


def register(app):
    async def lifecycle(app):
        host = current()
        if host.state()["enabled"]:
            await host.start()
        yield
        await host.stop(disarm=False)

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/api/capabilities/platform/inference-host", endpoint)
    app.router.add_post("/api/capabilities/platform/inference-host", endpoint)

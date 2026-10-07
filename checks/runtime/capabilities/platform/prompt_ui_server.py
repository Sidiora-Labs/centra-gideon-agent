import asyncio
import json

from aiohttp import web

from gideon.interfaces.dashboard.handlers import prompts
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware


async def main():
    app = web.Application(middlewares=[token_auth_middleware()])
    app.router.add_post("/api/prompts", prompts.api_prompt_create)
    app.router.add_put("/api/prompts/bindings", prompts.api_prompt_bindings_save)
    app.router.add_get("/api/prompts/{name}", prompts.api_prompt_detail)
    app.router.add_delete("/api/prompts/{name}", prompts.api_prompt_delete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{runner.addresses[0][1]}",
                "token": generate_token("prompt-owner"),
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())

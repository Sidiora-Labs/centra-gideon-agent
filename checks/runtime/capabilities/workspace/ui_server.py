import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json
import os
import tempfile

from aiohttp import web

from checks.runtime.capabilities.workspace.test_workspace import repository


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.environ["GIDEON_HOME"] = directory
        repo = repository(root / "repo")
        (root / "config.json").write_text(
            json.dumps({"dashboard": {"terminal": {"enabled": False}}})
        )
        from gideon.interfaces.dashboard.handlers.capabilities_workspace import register
        from gideon.interfaces.dashboard.token_auth import (
            generate_token,
            reset_secret_cache,
            token_auth_middleware,
        )

        reset_secret_cache()
        app = web.Application(middlewares=[token_auth_middleware()])
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{port}",
                    "repo": str(repo),
                    "token": generate_token("workspace-browser"),
                }
            ),
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

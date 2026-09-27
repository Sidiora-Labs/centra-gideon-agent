import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

from aiohttp import web


async def main(origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-studio-test-") as directory:
        home = Path(directory)
        os.environ["GIDEON_HOME"] = str(home)
        from gideon.interfaces.dashboard import token_auth
        from gideon.security.auth import credentials

        (home / "config.json").write_text(json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8")
        credentials.set_password("studio-owner", "correct-horse-battery-staple")
        token_auth.use_ephemeral_secret()

        from gideon.interfaces.dashboard.handlers import auth
        from gideon.workspace.artifacts.native import NativeArtifactProvider
        from gideon.workspace.capabilities.media.library_http import register_library

        artifacts = NativeArtifactProvider(home / "artifacts")
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg=="
        )
        artifact = artifacts.create_binary(
            name="Studio harbor image", data=image, mime="image/png", kind="image",
            source="manual", slug="studio-harbor-image",
        )
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
        app["port"] = 10000
        app["allowed_origins"] = {origin}
        app.router.add_post("/api/auth/login", auth.api_auth_login)
        app.router.add_get("/api/auth/session", auth.api_auth_session)
        register_library(app, artifacts)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets if site._server else []
        port = sockets[0].getsockname()[1]
        print(json.dumps({"api_port": port, "artifact_id": artifact.slug, "version": artifact.version}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

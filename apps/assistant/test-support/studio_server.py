import asyncio
import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web


async def main(origin: str) -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-studio-test-") as directory:
        home = Path(directory)
        os.environ["GIDEON_HOME"] = str(home)
        from gideon.core.config.loader import AppConfig
        from gideon.engine.session import ConversationDirectory
        from gideon.interfaces.dashboard import token_auth
        from gideon.interfaces.dashboard.state import ConsoleState
        from gideon.security.auth import credentials

        image_key = os.environ.pop("GIDEON_TEST_IMAGE_API_KEY", "")
        if not image_key:
            raise RuntimeError("The isolated image journey requires a provider credential")
        (home / "config.json").write_text(json.dumps({
            "auth": {"login_enabled": True},
            "providers": [{"name": "OpenAI", "type": "openai", "model": "gpt-image-1"}],
        }), encoding="utf-8")
        (home / "active_models.json").write_text(
            json.dumps({"image_gen": ["OpenAI:gpt-image-1"]}), encoding="utf-8"
        )
        from gideon.integrations.image_gen.openai_provider import OpenAIImageProvider
        from gideon.integrations.image_gen.registry import register_provider
        from gideon.integrations.media_catalogs import MediaCatalog, MediaModel, register_media_catalog

        register_media_catalog("image_gen", "openai", MediaCatalog(
            models=(MediaModel("gpt-image-1", extra={"sizes": ["1024x1024"], "supports_edit": True}),),
            default_model="gpt-image-1",
        ))
        register_provider(OpenAIImageProvider(provider_name="OpenAI", provider_type="openai", api_key=image_key))
        credentials.set_password("studio-owner", "correct-horse-battery-staple")
        token_auth.use_ephemeral_secret()

        from gideon.interfaces.dashboard.handlers import auth
        from gideon.workspace.artifacts.native import NativeArtifactProvider
        from gideon.workspace.artifacts import registry
        from gideon.workspace.artifacts.handlers import register_artifact_routes
        from gideon.interfaces.dashboard.handlers.capabilities_media import register as register_media, STORE_KEY
        from gideon.workspace.capabilities.media.jobs import MediaWorker
        from gideon.workspace.capabilities.media.jobs_http import JOBS_KEY

        artifacts = NativeArtifactProvider(home / "artifacts")
        registry.register_provider(artifacts)
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
        app["state"] = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
        app.router.add_post("/api/auth/login", auth.api_auth_login)
        app.router.add_get("/api/auth/session", auth.api_auth_session)
        register_artifact_routes(app)
        register_media(app)
        sketch = app[STORE_KEY].create({"width": 16, "height": 16, "request_id": "studio-seeded-sketch"})
        job = app[JOBS_KEY].submit({"operation": "sketch_export", "sketch_id": sketch["id"],
                                    "revision": sketch["revision"], "request_id": "studio-seeded-export"})
        MediaWorker(app[JOBS_KEY]).run_once(SimpleNamespace(should_stop=lambda: False))
        completed = app[JOBS_KEY].get(job["id"])

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        stop = SimpleNamespace(stopped=False, should_stop=lambda: stop.stopped)
        worker = MediaWorker(app[JOBS_KEY])

        async def run_worker():
            while not stop.stopped:
                await asyncio.to_thread(worker.run_once, stop)
                await asyncio.sleep(0.2)

        worker_task = asyncio.create_task(run_worker())
        sockets = site._server.sockets if site._server else []
        port = sockets[0].getsockname()[1]
        print(json.dumps({"api_port": port, "artifact_id": artifact.slug, "version": artifact.version,
                          "completed_job_id": completed["id"], "completed_artifact": completed["result"]}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            stop.stopped = True
            await worker_task
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

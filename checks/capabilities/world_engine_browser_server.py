import asyncio
import json
import os
from pathlib import Path
import subprocess

from aiohttp import web


ROOT = Path.cwd().resolve()
HOME = Path(os.environ["GIDEON_HOME"]).resolve()
DEPS = Path(os.environ.get("WORLD_ENGINE_DEPS", "/tmp/gideon-world-engine-deps")).resolve()
MODE = os.environ.get("WORLD_BROWSER_MODE", "source")
if MODE != "source":
    raise RuntimeError("The source world-engine browser server requires WORLD_BROWSER_MODE=source")


def install_engine():
    from gideon.extensions.apps import app_manager
    from gideon.extensions.apps.backend_runtime import get_backend_supervisor
    from gideon.extensions.apps.manager import app_data_dir
    from gideon.workspace.capabilities.experience.world_engine import APP_ID

    local = HOME / "engine-dependencies"
    local.mkdir(parents=True, exist_ok=True)
    for name in ("worlds", "video", "bun-linux-x64"):
        target = local / name
        if not target.exists():
            subprocess.run(["cp", "-a", "--reflink=auto", str(DEPS / name), str(target)], check=True)
    os.environ["PATH"] = str(local / "bun-linux-x64") + os.pathsep + os.environ["PATH"]
    template = ROOT / "runtime/gideon/workspace/capabilities/experience/assets/world-engine"
    installed = app_manager.install(template, confirm=True)
    if not installed.ok:
        raise RuntimeError(installed.error)
    get_backend_supervisor().stop(APP_ID)
    (app_data_dir(APP_ID) / "engine.json").write_text(json.dumps({"worlds": str(local / "worlds"), "video": str(local / "video")}))


def harness_page(request):
    if request.query.get("probe") == "1":
        return web.Response(text="<!doctype html><title>World renderer capability probe</title>", content_type="text/html")
    backend = "&webgl=1" if request.query.get("backend") == "webgl" else ""
    base = "/api/capabilities/experience/world-engine/host/?world=browser-proof&avatar=eidoverse/assets/vrms/claude.vrm" + backend
    return web.Response(text=f'''<!doctype html><html><body style="margin:0;background:#111;display:grid;grid-template-columns:1fr 1fr;height:100vh">
      <iframe id="alice" name="alice" allow="webgpu; fullscreen" src="{base}&name=alice"></iframe>
      <iframe id="bob" name="bob" allow="webgpu; fullscreen" src="{base}&name=bob"></iframe>
    </body></html>''', content_type="text/html")


async def main():
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "process-home").mkdir(exist_ok=True)
    os.environ["HOME"] = str(HOME / "process-home")
    install_engine()
    from gideon.workspace.capabilities.experience import ExperienceStore
    from gideon.workspace.capabilities.experience.world_engine_http import register_world_engine
    inner = web.Application()
    register_world_engine(inner, ExperienceStore(HOME))
    inner.router.add_get("/world-browser", harness_page)
    inner_runner = web.AppRunner(inner)
    await inner_runner.setup()
    inner_site = web.TCPSite(inner_runner, "127.0.0.1", 0)
    await inner_site.start()
    port = inner_site._server.sockets[0].getsockname()[1]
    print(json.dumps({"port": port, "mode": MODE}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        from gideon.extensions.apps.backend_runtime import get_backend_supervisor
        get_backend_supervisor().stop("gideon-world-engine")
        await inner_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

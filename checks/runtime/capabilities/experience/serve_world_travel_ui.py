import asyncio
import json
import signal
import sys
from pathlib import Path

from aiohttp import web

from gideon.extensions.apps import app_manager
from gideon.extensions.apps.backend_runtime import get_backend_supervisor
from gideon.extensions.apps.manager import app_data_dir
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.world_engine import APP_ID, WorldEngine
from gideon.workspace.capabilities.experience.world_travel import SCOPE
from gideon.workspace.capabilities.experience.world_travel_http import register_world_travel
from gideon.workspace.capabilities.experience.worlds import get_worlds
from gideon.workspace.capabilities.platform.peers import PeerStore


def record(identity, endpoint, label):
    return {"label":label, "endpoint":endpoint, "public_key":identity["public_key"], "enabled":True,
            "send_categories":[SCOPE], "receive_categories":[SCOPE], "revision":0}


async def serve(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])


async def main():
    home = Path(sys.argv[1])
    store, engine = ExperienceStore(home / "runtime"), WorldEngine(ExperienceStore(home / "runtime"))
    template = Path(__file__).resolve().parents[4] / "runtime/gideon/workspace/capabilities/experience/assets/world-engine"
    installed = app_manager.install(template, confirm=True)
    assert installed.ok, installed.error
    get_backend_supervisor().stop(APP_ID)
    deps = Path("/tmp/gideon-world-engine-deps")
    (app_data_dir(APP_ID) / "engine.json").write_text(json.dumps({"worlds":str(deps / "worlds"), "video":str(deps / "video")}))
    assert (await engine.control("start", {}))["state"] == "running"
    worlds = get_worlds(store)
    await worlds.open("ui_lounge", {})
    origin, destination = PeerStore(home / "origin"), PeerStore(home / "destination")
    destination_app = web.Application()
    register_world_travel(destination_app, store, destination)
    destination_runner, destination_url = await serve(destination_app)
    origin_app = web.Application()
    register_world_travel(origin_app, store, origin)
    origin_runner, origin_url = await serve(origin_app)
    origin_identity, destination_identity = origin.snapshot()["self"], destination.snapshot()["self"]
    origin.put(destination_identity["peer_id"], record(destination_identity, destination_url, "Actual destination"))
    destination.put(origin_identity["peer_id"], record(origin_identity, origin_url, "Actual origin"))
    print(f"ORIGIN_URL={origin_url} DEST_URL={destination_url}", flush=True)
    stop = asyncio.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(name, stop.set)
    try:
        await stop.wait()
    finally:
        await origin_runner.cleanup()
        await destination_runner.cleanup()
        await worlds.close()
        get_backend_supervisor().stop(APP_ID)


if __name__ == "__main__":
    asyncio.run(main())

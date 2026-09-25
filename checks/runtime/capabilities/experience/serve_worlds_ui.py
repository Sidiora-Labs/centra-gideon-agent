import asyncio
import json
import signal
import sys
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_experience import STORE, register
from gideon.workspace.capabilities.experience import ExperienceStore
from gideon.workspace.capabilities.experience.world_engine import WorldEngine, APP_ID
from gideon.workspace.capabilities.identity.goals import GoalStore
from gideon.extensions.apps import app_manager
from gideon.extensions.apps.manager import app_data_dir
from gideon.extensions.apps.backend_runtime import get_backend_supervisor


async def main():
    home = Path(sys.argv[1])
    store = ExperienceStore(home)
    engine = WorldEngine(store)
    template = Path(__file__).resolve().parents[4] / 'runtime/gideon/workspace/capabilities/experience/assets/world-engine'
    result = app_manager.install(template, confirm=True)
    assert result.ok, result.error
    get_backend_supervisor().stop(APP_ID)
    deps = Path('/tmp/gideon-world-engine-deps')
    (app_data_dir(APP_ID) / 'engine.json').write_text(json.dumps({'worlds':str(deps/'worlds'),'video':str(deps/'video')}))
    assert (await engine.control('start', {}))['state'] == 'running'
    GoalStore(home / 'capabilities/identity/goals.sqlite3').save_goal(title='Actual authored goal', request_id='ui_goal')
    app = web.Application()
    app[STORE] = store
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print('UI_URL=http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1]), flush=True)
    stop = asyncio.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(name, stop.set)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()
        get_backend_supervisor().stop(APP_ID)


if __name__ == '__main__':
    asyncio.run(main())

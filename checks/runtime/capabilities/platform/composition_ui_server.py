import asyncio
from aiohttp import web
from gideon.interfaces.dashboard import views_store as store
from gideon.interfaces.dashboard.handlers.capabilities_compositions import register
from gideon.interfaces.dashboard.handlers import views
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    store.create_view('Operations')
    store.add_tile('overview', 'artifact:overview-only')
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    app.router.add_get('/api/dashboard/views', views.api_dashboard_views)
    app.router.add_post('/api/dashboard/views/{view_id}/tiles/resolve', views.api_dashboard_view_tile_resolve)
    app.router.add_post('/api/dashboard/views/{view_id}/tiles', views.api_dashboard_view_tiles)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())

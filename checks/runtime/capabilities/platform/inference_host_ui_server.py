import asyncio
import sys
from aiohttp import web
from gideon.core.config.loader import config_dir
from gideon.integrations.llm.credentials import CredentialStore
from gideon.integrations.llm.registry import get_default_registry, ProviderEntry
from gideon.interfaces.dashboard.handlers.capabilities_inference_host import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware
from gideon.integrations.local_models.sidecar import SidecarRunner, register_runner, unregister_runner


async def main():
    CredentialStore(config_dir()).put('peer-key', {'type':'static_token','value':'qualification-listener-peer-secret'})
    get_default_registry().register_entry(ProviderEntry(name='Unavailable inference',type='uninstalled-model-type',model='declared-model'))
    register_runner(SidecarRunner(app='Unavailable GPU runtime', worker=config_dir() / 'missing-runtime-worker.py', python=sys.executable))
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner,'127.0.0.1',0).start()
    print(runner.addresses[0][1],flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        unregister_runner('Unavailable GPU runtime')


asyncio.run(main())

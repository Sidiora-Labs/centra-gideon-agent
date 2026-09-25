"""Run the real dashboard bootstrap in an isolated qualification home."""
import asyncio
import json
import os
from pathlib import Path
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.server import start_dashboard
from gideon.interfaces.dashboard.token_auth import generate_token, reset_secret_cache
from gideon.automation.triggers.nudge import AutoNudgeService

async def main():
    Path(os.environ['GIDEON_HOME']).mkdir(parents=True,exist_ok=True)
    reset_secret_cache()
    runner,state=await start_dashboard(ConversationDirectory(AppConfig()),port=0,local_only=True,dashboard_url=os.environ['GIDEON_BROWSER_ORIGIN'])
    nudge=AutoNudgeService()
    await nudge.start()
    site=next(iter(runner.sites))
    port=site._server.sockets[0].getsockname()[1]
    print(json.dumps({'port':port,'token':generate_token('browser-owner')}),flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        nudge.stop()
        await runner.cleanup()
        state.knowledge_store.close()

if __name__=='__main__':asyncio.run(main())

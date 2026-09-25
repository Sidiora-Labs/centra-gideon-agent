import asyncio
import time
from aiohttp import web
from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.core.config.loader import config_dir
from gideon.engine.trigger_outcomes import FireResult
from gideon.sdk.tool import ToolResult
from gideon.interfaces.dashboard.handlers.capabilities_cadence import register
from gideon.interfaces.dashboard.token_auth import token_auth_middleware


async def main():
    trigger = Trigger(id='audit-one', name='Audit one', kind='clock', spec={'kind': 'interval', 'interval_secs': 60}, workflow={'ref': 'audit-workflow'})
    TriggerStore().upsert(trigger)
    for index in range(5):
        result = FireResult.read(ToolResult(success=False, error='Task verification failed'), None)
        stamp = time.time() - 10 + index
        await ExecutionJournal(config_dir()).append(ExecutionRecord(run_id=str(index), job_id=trigger.id, trigger=result.exit_type, status='failure', started_at=stamp, finished_at=stamp))
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    print(runner.addresses[0][1], flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


asyncio.run(main())

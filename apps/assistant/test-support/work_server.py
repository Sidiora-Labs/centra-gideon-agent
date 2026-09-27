import asyncio
import json
import os
import tempfile

from aiohttp import web


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gideon-assistant-work-") as directory:
        os.environ["GIDEON_HOME"] = directory

        from gideon.engine.tasks import registry
        from gideon.engine.tasks.handlers import register_task_routes
        from gideon.engine.tasks.hierarchy import HierarchyStore
        from gideon.engine.tasks.native import NativeTaskProvider

        registry._providers = {"native": NativeTaskProvider()}
        project = HierarchyStore().create_project("Source project")
        task = await registry.create_task(title="Source task", description="Durable native task")

        app = web.Application()
        register_task_routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        print(json.dumps({"port": port, "task_id": task.id, "project_id": project.id}), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import json
import sys
from gideon.workspace.capabilities.workspace.processes import ProcessRegistry


async def main():
    registry = ProcessRegistry(sys.argv[1], allowed_roots=[sys.argv[2]])
    row = await registry.start({'project_id':'crash-worker','workspace':sys.argv[2],'command':'exec sleep 60','request_id':'actual-crash'})
    print(json.dumps({'id':row['id'],'pid':registry.handles[row['id']].pid}), flush=True)
    await asyncio.Event().wait()


asyncio.run(main())

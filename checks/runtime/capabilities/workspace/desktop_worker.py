import asyncio
import json
import sys
from pathlib import Path
from gideon.workspace.capabilities.workspace.desktop import DesktopRegistry


async def main():
    root=Path(sys.argv[1])
    registry=DesktopRegistry(root/'state',allowed_roots=[root])
    result=await registry.start({'project_id':sys.argv[2],'request_id':'crash','width':800,'height':600})
    handle=registry.handles[result['id']]
    print(json.dumps({'record':result,'display':handle['display']}),flush=True)
    await asyncio.Event().wait()


asyncio.run(main())

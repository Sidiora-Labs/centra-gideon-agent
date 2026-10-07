# gideon-client

An async Python HTTP client for a running Gideon gateway. This package has its own
`pyproject.toml`, requires Python 3.9 or later, and depends on `aiohttp`. It is separate
from the in-process app provider SDK (`gideon.sdk.*`) in the gateway package.

## Install from this checkout

Run from the repository root, using your chosen Python environment:

```sh
python -m pip install ./packages/python-client
```

## Read gateway status

Supply the address and an authorized token explicitly. Keep tokens out of source control
and logs. The client sends its token as the `gideon_token` cookie.

```python
import asyncio
import os
from gideon_client import GideonClient

async def main():
    async with GideonClient(
        base_url=os.environ["GIDEON_URL"],
        token=os.environ["GIDEON_TOKEN"],
    ) as client:
        status = await client.get_status()
        print(status)

asyncio.run(main())
```

An installed app can pass `app_name` to use the local app-secret exchange where
available. An app name by itself is not an authorization credential. Remote connections
require a token; route access still depends on the gateway's current permissions.

The client also exposes chat, subagent, schedule, notification, configuration, and
memory methods. Method availability does not guarantee permission to use a route.
In particular, the current transport does not add an `Origin` or `Referer` header to
mutating requests, while the gateway checks those headers on unsafe methods. Check
compatibility with your gateway before relying on write operations; successful status
reads do not establish that writes will be admitted.

`get_app_data_dir()` computes a local filesystem path. It neither creates the directory
nor fetches a remote gateway's files.

## Errors

HTTP and transport failures raise `GideonError`, with a machine-readable `ErrorCode` and
optional response status/body. `ping()` instead returns `False` on failure. See
[src/gideon_client/client.py](src/gideon_client/client.py) and
[src/gideon_client/transport.py](src/gideon_client/transport.py) for the current methods,
authentication, and retry behavior.

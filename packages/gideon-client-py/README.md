# gideon-client

An async Python client for the [Gideon](https://gideon.dev) Gateway.

`gideon-client` is the small, dependency-light library that apps and
external tools use to talk to a running Gideon gateway — send messages,
read status, resolve an app's data directory — without vendoring the core
package. It is versioned and published independently of the core gateway so a
tool can pin a stable client contract.

## Install

```sh
pip install gideon-client
# or
uv pip install gideon-client
```

## Usage

```python
from gideon_client import GideonClient

async with GideonClient(app_name="my-app") as pc:
    ok = await pc.ping()
    status = await pc.get_status()
    await pc.send_message("session-1", "hello")
```

## Errors

Client calls raise `GideonError` (with a machine-readable `ErrorCode`) on
gateway/transport failures — catch it to distinguish "gateway unreachable" from
"bad request".

## Links

- Homepage: https://gideon.dev
- Source & issues: https://github.com/Gideon/Gideon
- License: MIT

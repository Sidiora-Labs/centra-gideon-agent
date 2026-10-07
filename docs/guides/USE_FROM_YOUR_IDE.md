# Using Gideon from your editor

Gideon's configured inbound MCP surface exposes read-only tools over `POST /mcp`.
It is separate from the outbound MCP servers your Gideon agent uses and from the
internal agent MCP entrypoints. The source is
`runtime/gideon/integrations/inbound/mcp_http.py` and `tools.py`.

## Local setup

Use a separate surface credential rather than the dashboard owner token:

```bash
gideon inbound token create mcp
gideon config set external_access.enabled true
gideon config set external_access.mcp.enabled true
```

Copy the printed token into your client's protected credential configuration. It is
shown on creation, not reprinted by `gideon inbound token show mcp`. To replace it:

```bash
gideon inbound token create mcp --rotate
```

Restart the gateway if the route was not mounted at startup. Disablement is checked
per request, so an already-mounted route can be stopped with either switch:

```bash
gideon config set external_access.mcp.enabled false
gideon config set external_access.enabled false
```

The master switch affects all inbound surfaces. Client disablement and incident policy
are additional admission checks. A configured token does not override a disabled surface.

## Client configuration

Use a client capable of streamable HTTP POST transport and an authorization header.
Client configuration syntax differs, but the required local values are:

| Setting | Value |
|---|---|
| URL | `http://127.0.0.1:10000/mcp` (use your actual gateway port) |
| Transport | Streamable HTTP; not stdio and not a GET SSE subscription |
| Authentication | `Authorization: Bearer <MCP surface token>` |

A client-specific example is:

```json
{
  "mcpServers": {
    "gideon": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:10000/mcp",
      "headers": {"Authorization": "Bearer REPLACE_WITH_SURFACE_TOKEN"}
    }
  }
}
```

Check the actual editor's supported configuration rather than treating those key names
as universal. Protect the resulting file; the bearer token grants the configured client
reach. A dashboard token and an inbound token have different audiences and must not be
reused interchangeably.

## Read contract

The inbound registry currently provides `status`, `memory_recall`, `knowledge_search`,
`sessions_search`, `tasks_list` and `task_get`. No generic write tool is exposed by
this registry. Client permissions and applicable native scope/privacy checks can further
restrict a result; a registered tool is not proof of access to every owner record.
Returned content is fenced as data, not instructions.

The server is POST-only. `GET /mcp` returns 405 for a mounted surface; 404 can indicate
an unmounted or disabled surface. Supported protocol negotiation is implemented in
`mcp_http.py`; do not hardcode a newer protocol version merely because a client supports it.

## Check the actual connection

The base runtime includes the MCP SDK. From a prepared Python environment, probe the
configured local surface with its token supplied through an environment variable:

```python
import asyncio
import os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client(
        os.environ.get("GIDEON_MCP_URL", "http://127.0.0.1:10000/mcp"),
        headers={"Authorization": f"Bearer {os.environ['GIDEON_MCP_TOKEN']}"},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            print(initialized.serverInfo.name, initialized.protocolVersion)
            print([tool.name for tool in (await session.list_tools()).tools])
            print((await session.call_tool("status", {})).isError)

asyncio.run(main())
```

This checks the actual transport, initialization, tool listing and one status call.
It does not qualify memory access, every editor configuration or remote delivery.

## Remote boundary and failures

The default peer policy is loopback. A configured remote surface additionally requires
`allow_remote`, the public URL/Host contract and valid client authentication. Global
origin checks precede unsafe POST handlers: a non-loopback request without an admitted
Origin or Referer is refused. Setting `allow_remote` alone does not bypass that check.
The local recipe avoids that extra boundary; this guide does not certify an off-machine
MCP client or suggest weakening origin admission to make one work.

| Response | Investigate |
|---|---|
| 404 | Startup mount, master/per-surface enablement and token configuration |
| 401 | Actual surface bearer token and client registration/revocation |
| 403 | Peer, origin, Host and permitted operation/scope |
| 405 on GET | Use POST MCP transport; no GET SSE stream |
| 429 | Respect the returned rate-limit/retry policy and reconnect if required by the client |
| 503 | Incident policy or unavailable service; inspect the actual response |
| JSON-RPC unknown tool | Compare the name with the admitted tool registry |

See [Remote access](REMOTE_ACCESS.md) and [Security architecture](../architecture/SECURITY.md).

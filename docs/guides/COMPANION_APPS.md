# Companion apps

A companion is another device accessing a Gideon gateway. It needs both a reachable
address and authorized access; finding an address does not grant permission. The shared
dashboard supplies the main UI, while desktop and mobile shells own their native storage,
navigation, and platform integration.

## Reach a gateway

The gateway normally binds to loopback. To permit network access, bind the intended
interface and keep authentication enabled. Binding all interfaces is explicit:

```bash
GIDEON_BIND_HOST=0.0.0.0 gideon gateway --port 10000
```

Confirm the address and firewall reachability on the intended network. This exposes the
listener on all interfaces; it does not create TLS, a tunnel, or a private network.
For internet access, use the authenticated TLS/proxy configuration in
[remote access](REMOTE_ACCESS.md). Never enable local-network authentication bypass
for an internet-facing path.

An authenticated owner can pair a device through Settings → Devices, or use the
supported enrollment flow. `gideon auth enroll` creates a short-lived, single-use code;
it is not a reusable password. The served pairing page performs the exchange so the
session cookie reaches the same browser or WebView that will load the dashboard.

A `gideon token` link contains a bearer credential. Keep it private and do not store it
as an endpoint label or log it. Query-token access can bind to the first client address;
paired cookie sessions are the appropriate durable device path. Revocation applies to
the actual gateway session, not every entry in a client's gateway list.

Unsafe HTTP operations and WebSocket upgrades have origin checks in addition to token
validation. A page that loads successfully may still be refused when it writes or opens
a socket. Configure the actual admitted dashboard origin; do not bypass the checks or
assume changing a hostname repairs every network issue.

## Optional discovery

LAN discovery is disabled by default. To enable it:

```bash
gideon config set companion.discovery_enabled true
gideon config set companion.instance_name "My gateway"
gideon discover --json
```

The DNS-SD service advertises `_gideon._tcp` with bounded discovery metadata: name,
port, pairing requirement, and schema version. It does not advertise an access token
or session content. The current source is
`runtime/gideon/integrations/companion/discovery.py`.

Discovery requires a usable network address and a reachable listener. Networks may
filter multicast or isolate clients, so a typed address remains useful. Discovery is
unauthenticated: verify the destination before entering a pairing code. A discovered
lookalike is not made trustworthy by a matching display name.

## Shared UI and endpoint storage

The served console uses origin-relative API and WebSocket requests. A shell switches
gateways by navigating to the chosen origin, not by prepending an unrelated base URL to
those requests. Loading gateway A's dashboard does not make gateway B's data available.

The endpoint vocabulary is defined in
`apps/console/src/shared/data/endpoints.ts`: an active pointer and endpoint rows with
stable client IDs, labels, addresses, and optional device-session references. The desktop
shell maintains its own protected registry; the mobile bootstrap maintains its address
registry. Keep credentials in the appropriate cookie/session store, not in those rows.

Origin isolation partitions served-page storage, but wrapper-owned storage still needs
explicit endpoint isolation. Do not carry cached counts, routes, or credentials into a
different endpoint. A switcher is navigation among gateways, not a merged inbox or a
shared authorization system.

The desktop has a gateway switcher and endpoint health handling. The mobile bootstrap
remembers and opens a selected address; it does not independently implement every
desktop switcher feature. Describe the actual shell rather than treating this shared
format as proof that all shells have identical controls.

## Connections and reconnect

A WebView loading the dashboard inherits its socket reconnect behavior. Avoid adding a
competing wrapper retry timer for the same connection. Shell-level endpoint health can
still report DNS, connection, or authentication failures separately.

A native client opening `/api/ws` directly must follow the gateway's socket admission
contract. The server permits absent Origin only for a validated paired-device session;
an ordinary session without Origin is refused, and a present foreign Origin is refused.
That narrow socket rule does not exempt unsafe HTTP methods from their origin check.

Use HTTPS/WSS for remote public origins. Set both `dashboard.url` and
`dashboard.public_url` to the actual public origin where required: the former supplies
admitted dashboard origins, while the latter describes public access. Token authentication
is required when extending the dashboard origin. A native socket session and a WebView
page have different header behavior, so test the path you actually deploy.

## Native shells and limits

The [desktop shell](DESKTOP.md) adds local capabilities only on its trusted local bridge;
its packaged Windows mode uses a configured hosted origin. The
[mobile shell](../../apps/mobile/README.md) loads the served companion route using
Capacitor and has explicit navigation restrictions.

A device session, reachable gateway, or successful contract test does not prove native
permission prompts, safe areas, notification display, or release signing. Those need
actual platform verification. See the operator's release instructions and report the
specific platform and journey exercised.

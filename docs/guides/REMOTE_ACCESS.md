# Remote access to your gateway

This guide covers remote access to the OSS self-hosted gateway. Gideon also
offers a hosted service; this is not its setup guide.
Connecting another device does not grant the agent additional tools. Authentication, origin checks,
transport encryption and execution permissions remain separate.

Start from the access URL printed by your gateway or `gideon token`. Treat the complete
tokenized URL as a credential; do not publish it or include it in shared screenshots.

## Private network

A configured private overlay or VPN can connect your devices without publishing the
gateway to the internet. Gideon does not provision that network. Use its actual address
and firewall policy, keep `GIDEON_AUTH_MODE=local_token`, and bind to an interface that
the intended device can reach.

For example, an explicitly configured network listener is:

```bash
GIDEON_AUTH_MODE=local_token GIDEON_BIND_HOST=0.0.0.0 gideon gateway --port 10000
```

`0.0.0.0` listens on all available interfaces, not just a private overlay. Restrict reach
with the host's actual network/firewall configuration. A private network does not replace
gateway authentication. None mode forces loopback and is not a remote-access mode.

Do not enable `GIDEON_BYPASS_LOCAL_NETWORKS=1` for a public proxy. A proxy on a private
address can make remote traffic look local; that bypass cannot prove where the original
client came from. Development `make serve-lan` explicitly uses that bypass and is not
a hardened remote deployment recipe.

## TLS reverse proxy or tunnel

Gideon's dashboard does not terminate public TLS. Supply a TLS-terminating proxy/tunnel
and keep the gateway behind it. Configure the actual advertised origin and trusted proxy
peers, for example:

```json
{
  "dashboard": {
    "url": "https://gideon.example.com",
    "public_url": "https://gideon.example.com",
    "trusted_proxies": ["127.0.0.1"]
  }
}
```

Use the proxy's actual connecting address rather than copying this value blindly.
`runtime/gideon/security/exposure.py` resolves public URL, trusted forwarding, cookie
and CSP behavior. Dashboard origin admission is implemented in
`runtime/gideon/interfaces/dashboard/origin.py` and `server.py`. Public URL, allowed
browser origin and proxy forwarding must agree; TLS configuration alone does not make
an origin admissible.

A proxy that terminates TLS can see the traffic. Use a deployment appropriate to the
account, state and services this gateway can access. This guide does not certify a
proxy, tunnel provider or public deployment.

## Owner login and device enrollment

Owner password login is additional to the local token path:

```bash
gideon auth set-password
gideon auth enable
gideon auth status
```

The credential store persists an Argon2id password hash, not plaintext. CLI enrollment
and second-factor commands are available through `gideon auth --help`:

```bash
gideon auth totp setup
gideon auth enroll
gideon auth revoke --all
```

Enroll and verify the actual authenticator before requiring TOTP. Device codes are
single-use, hashed at rest and expire; they mint an ordinary session, not a separate
admin privilege. Revoke sessions for a lost device. Login lockout and token/session
revocation are different controls. Check actual status and configured policy instead of
assuming a successful password change removed every old session.

Container first-boot owner credential seeding is described in [Containers](CONTAINERS.md).

## Phone notifications

Phone push is configured separately from remote connectivity. The runtime supports
configured web push, ntfy and relay paths; actual subscriptions, credentials and reachable
services determine delivery. `gideon push init`, `status` and `test` expose setup and
send diagnostics. A successful send is not proof that a handset displayed a notification.

Push notifications carry identifiers for the device to fetch the protected item from
your gateway. They do not themselves approve tools. A relay or native store application
must be supplied and deployed with its own signing/service credentials; this repository
does not establish a hosted relay's availability. Browser push requires the actual
browser's secure-context and permission support. Plain HTTP on a non-loopback address
must not be assumed to support service workers or microphone access.

## Troubleshooting

- **Cannot reach the address:** check the gateway's bound interface/port, host firewall,
  proxy forwarding and the device's actual network route.
- **Page loads but writes or WebSocket fail:** check allowed origins, advertised URL,
  proxy configuration and the actual response. Do not disable origin checks to hide it.
- **Login cookie is dropped:** verify HTTPS/public URL and cookie policy agree.
- **Locked out:** inspect local auth status and the configured lockout; remote access
  does not provide a privileged bypass.
- **Push did not arrive:** distinguish gateway send status from subscription validity,
  relay/provider response and actual device delivery.

See [Companion apps](COMPANION_APPS.md), [Use from your editor](USE_FROM_YOUR_IDE.md),
[Threat model](../security/THREAT_MODEL.md) and the [security policy](../../SECURITY.md).

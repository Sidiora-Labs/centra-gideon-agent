# Security limitations

Gideon's controls have different boundaries. Gateway authentication, operation grants,
content scanning, scoped memory and child-process policy do not together make arbitrary
installed code safe. This page describes the limits of the implemented source; it is not
a penetration-test result or deployment certification.

## 1. External agent permissions depend on the actual provider

The native runtime checks task modes, tool and skill grants, app work tiers and approval
policy before invoking tools. Its controls live under `runtime/gideon/engine` and
`runtime/gideon/security`.

ACP permission behavior is provider-dependent. `integrations/acp/permission_authority.py`
normalizes requested modes: elevated or unknown modes are clamped for attended work;
unattended work can admit recognized auto-approve modes. The host can gate operations
that the provider reports through ACP permission requests. It cannot promise equivalent
coverage for operations a CLI executes without reporting them. The measured residual
registry and [ACP comparison](../agents/acp-parity.md) describe that distinction.
Prompt instructions alone are not enforcement.

## 2. The app `network` permission is declaration-only

`runtime/gideon/extensions/apps/permissions.py` exposes `can_use_network` as a declaration.
It does not contain every network request an app might make. In-process Python can open
its own sockets, and an app backend is a separate program. Install review therefore
labels network use as disclosure, rather than treating `network: false` as containment.

Gateway-mediated HTTP and command execution have additional egress controls. In
`runtime/gideon/security/sandbox.py`, restricted command egress requires an actual
OS no-network wrapper or refuses before launch if the host cannot supply it. A declared
external program uses a separate wrapping path. These controls do not turn the app
permission into general socket isolation, nor provide arbitrary per-host filtering for
all command-line programs.

## 3. App dependencies share one writable prefix, not an isolated interpreter

App `dependencies.pythonDependencies` are installed into `<GIDEON_HOME>/app-python`
by `runtime/gideon/extensions/apps/app_python.py`, **not** into the gateway's base
virtual environment. The prefix is appended after the interpreter's own package paths.
Resolution considers installed app requirements and constrains distributions already
provided by the gateway; `app_manager.py` also rejects conflicting core requirements.

This protects the base environment from ordinary app dependency replacement. It does
not give each in-process provider its own interpreter or private module namespace for
third-party dependencies. Apps share one Python import environment and mutually
incompatible requirements can be refused. Packages and their install process remain
code you must trust. Rebuilding the derived prefix is distinct from restoring app data.

## 4. App UI shares the host origin

A contributed UI mounts in the console's React tree. It has access to the host DOM and
same-origin requests. Browser access to an HttpOnly cookie's raw value is restricted,
but the cookie can still authorize same-origin requests from that page. The SDK's
path checks constrain cooperating clients; they do not contain arbitrary JavaScript.
Server app permissions apply when the request carries a verified app-scoped identity.

`uiCapabilities` controls supported SDK imports and discloses host integrations. It is
not an iframe, separate origin or JavaScript sandbox. Trust a UI-bearing app with that
browser authority before installing it.

## 5. Sandboxing and local authority have platform limits

Child environment filtering and credential-path hiding are not a general filesystem
jail. Available OS wrappers, configured sandbox mode and execution site determine
what is applied. Network-off command wrapping is a separate restriction; unrestricted
execution does not gain it merely because a sandbox function was called. An installed
in-process provider is not contained by those child wrappers.

The owner or a process with the owner's file access can change installed code,
configuration and credentials. Audit chains and packaged baseline checks detect certain
changes inside the expected trust model; they do not defend against a compromised OS
or an attacker who controls both state and the verification keys.

## 6. Privacy requires the actual work scope and lifetime

Temporary work suppresses persistent memory reads and writes. Incognito permits
otherwise authorized reads but suppresses writes. Session labels alone do not establish
scope: `runtime/gideon/security/session_credentials.py` and native Hypermid scope issuers
validate current origin and authority. Private workflow resources require the live native
receipt and lifecycle cleanup. A missing issuer or unavailable proof must not be replaced
with a guessed workspace or a different privacy mode.

Privacy controls do not prevent configured model or channel providers from receiving
requests needed to perform the work, and they do not erase provider-side logs. Review
the actual integration's data handling separately.

See [Threat model](THREAT_MODEL.md), [Security architecture](../architecture/SECURITY.md)
and [App platform](../architecture/APP_PLATFORM.md).

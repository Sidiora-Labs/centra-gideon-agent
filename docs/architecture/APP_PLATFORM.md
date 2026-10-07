# The app platform

Apps contribute models, agents, channels, search, speech, tools, actions and console
pages. The platform owns their manifests, review, installation, permissions and
lifecycle. Source paths in this document are relative to the repository root.

## Bundles and installed state

Bundled apps ship in `runtime/gideon/extensions/apps/native`. Native manifests are
seeded into the home's installed-app tree and refreshed from packaged source by
`runtime/gideon/extensions/apps/app_manager.py`. Other apps come from configured
catalogs, git sources or local bundle directories. Source examples live under
`examples`; they are not automatically installed or served.

The active installed copy is `<GIDEON_HOME>/apps/<name>`. Its metadata, enabled state,
manifest and consent determine availability. Editing a source directory alone does not
update that copy. Native lifecycle restrictions differ from ordinary user-installed apps;
do not infer uninstall or disable support from a catalog entry.

## Review, install and update

`extensions/apps/manifest.py` validates the manifest, including version requirements,
providers, permissions, programs, jobs and UI declarations. `core_features.py` checks
required core features. `disclosure.py` produces the shared review projection: permissions,
scheduled jobs, dependencies, launched programs, writes, providers, hooks, UI, MCP servers
and skills.

`POST /api/apps/preview` stages the offered bundle, scans it and returns its review and
`review_digest`. Install and update handlers pass that digest to `app_manager`; changed
staged bytes are refused with a new review. An update that changes the review requires
renewed consent. An unchanged review can proceed without an extra confirmation; it is
still checked against the offered staged bundle.

The manager stages source under the home's app quarantine, validates and scans it,
checks compatibility and signatures, prepares dependencies and lifecycle hooks, then
publishes the installed copy and registers its contributions. Dangerous scanner verdicts
and invalid signatures are refused. Warning verdicts require explicit confirmation.
Scanning is a content check, not proof that code is safe. Update keeps a rollback copy
while replacing the previous installation.

Disable withdraws runtime contributions; uninstall also removes the applicable installed
files. Memory namespace revocation and provider/skill/catalog cleanup are lifecycle work,
not merely a change to a card's enabled flag.

## Permissions and execution authority

`extensions/apps/permissions.py` evaluates the current installed app and consent.
Authentication supplies app identity; a request body or identity header alone does not.
Owner-only operations remain owner-only even if an app lists their paths in `api`.

| Declaration | Scope |
|---|---|
| `api` | Gateway path access, subject to endpoint ownership and operation rules |
| `events` | WebSocket event delivery |
| `eventSubscriptions` | Exact platform event subscriptions; separate from WebSocket events |
| `mcpTools` | Named MCP tool access |
| `storage` | App data directory supplied to a backend |
| `storageRead` / `sharedStorage` | Brokered shared-storage relationship; requires both sides' grants |
| `memory` | No grant when absent; `app-scoped` and `shared` are distinct memory scopes |
| `agent` | `text`, `read` or `tools` ceiling for app-origin agent work |
| `cron` | Permission to register declared scheduled jobs; also needs an agent tier |
| `appMessaging` | Brokered messages to declared app targets |
| `desktop` | Exact declared desktop capability names |
| `network` | Disclosure of network use, not general per-app network containment |

Agent tiers are defined in `extensions/apps/agent_tiers.py`:

- **text** supplies task text without tool execution or persistent-memory context.
- **read** admits operations classified as reads, subject to their own tool, scope and
  approval rules.
- **tools** admits tool-capable work under the remaining grants and approval policy.

`extensions/apps/app_work.py` intersects the held tier with the current enabled manifest.
Child work can narrow this ceiling and cannot widen it. Tool and skill selections remain
additional constraints. An install grant to start work does not automatically grant every
mutating tool. Live revocation and actual accepted trigger identity matter for scheduled
work; a string resembling an app job is not sufficient.

Memory access additionally requires a verified work origin, the current app permission
and a native authorized scope. App-scoped memory uses a native issued namespace rather
than a caller-selected workspace name. Temporary workflow starts require a live native
private-work receipt, and private resources are retired through the actual work lifecycle.
Temporary mode is not converted to Incognito to acquire access. App work cannot create
an unscoped persistent run through a general schedule or workflow starter.

## Python providers and subprocesses

`extensions/providers/loader.py` loads enabled app contributions. Bundle-relative modules
are loaded by `extensions/apps/native_contract.py` under namespaced module identities,
so two apps shipping `provider.py` do not share one module accidentally. The supported
Python integration surface is `gideon.sdk`, located in **`runtime/gideon/sdk`**.
`packages/python-client` is the separate gateway HTTP client.

A provider's execution declaration and actual registered model locality describe where
it runs. In-process Python runs with the gateway process's authority; import-boundary
checks and API grants do not make it an OS sandbox. Installed dependency environments,
backend children and declared external programs have different execution paths.

`extensions/apps/backend_runtime.py` supervises backend subprocesses;
`worker_runtime.py` supervises app workers. Child environments are built through the
sandbox environment allowlist rather than copying all gateway credentials. Backend
variables include the resolved port, app identity and proxy secret; the app data-directory
variable is supplied only for storage-enabled apps. Operator `sandbox.env_passthrough`
applies to child sites and should be configured with that breadth in mind.

The gateway proxies `/apps/{name}/api/{tail}` to a backend, strips owner credentials,
and supplies an app-scoped credential and proxy signature. The SDK helper
`gideon.sdk.security.require_proxy_signature()` verifies the signature over timestamp,
method, raw path and body digest; app backends must install that middleware to reject
direct unsigned requests. This is authorization, not loopback encryption or isolation
from another process that can read the owner's files.

Process output and exit status are exposed through the native app detail contract.
Program declarations and install hooks are disclosed execution paths, not promises that
all their filesystem or network effects are contained. See
[Security limits](../security/LIMITATIONS.md).

## Console SDK

`apps/console/src/app/shell/appSdk.tsx` supplies `@gideon/app-sdk`. A contributed page
receives host API/events and mounts inside the host React tree. Optional subpaths require
entries in `uiCapabilities`:

| Import | Declaration | Purpose |
|---|---|---|
| `@gideon/app-sdk/ui` | `shell-primitives` | Host buttons, surfaces and theme helpers |
| `@gideon/app-sdk/genui` | `generative-widget` | Render a host-validated generative widget |
| `@gideon/app-sdk/genui` | `generative-component` | Register contributed component types |

Component registration is additive and cannot shadow core component names. Disabled
apps lose their registered components. These declarations control supported imports and
make usage visible; a host-tree page shares the browser environment and is not an iframe
security sandbox.

## Jobs, MCP and skills

`extensions/apps/app_crons.py` reconciles declared jobs on lifecycle transitions. App jobs
retain canonical installed identity and declared work tier. Unsupported delivery or
execution posture is refused rather than silently converted. Owner edits to supported
cadence, message and job name do not themselves grant new capabilities.

`extensions/apps/mcp_bridge.py` registers app-shipped MCP servers using app-owned names
and removes those contributions on withdrawal. External MCP configuration, per-tool grants
and live inventory review are separate from a server merely being declared by an app.

Apps can contribute skills and marketplaces. Native skill loading lives in
`runtime/gideon/extensions/skills/loader.py`; integrity, selected content, namespace and
current agent/app skill limits still apply. Quality badges are manifest declarations,
not blanket verification of an app's tests, accessibility or deployment.

## Further reading

- [Provider boundary](PROVIDER_BOUNDARY.md)
- [Tasks and triggers](TASKS_TRIGGERS.md)
- [Security architecture](SECURITY.md)
- [Threat model](../security/THREAT_MODEL.md)
- [API overview](../reference/API_OVERVIEW.md)

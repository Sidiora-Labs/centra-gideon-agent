# Gideon architecture overview

This repository documents Gideon’s open-source, self-hosted edition. Gideon also
offers a hosted service. The OSS Python gateway coordinates conversations,
agent runtimes, tools, knowledge, memory, tasks, schedules, workflows and channels, and
serves the console over HTTP and WebSocket. Integrations and optional local services can
run additional processes; “one gateway” does not mean every capability runs in that process.

Paths below are relative to the repository root.

## Runtime and surfaces

`runtime/gideon/engine/gateway.py` defines the runtime coordinator;
`runtime/gideon/engine/lifecycle.py` starts and stops its services. The dashboard is
an aiohttp application assembled in `runtime/gideon/interfaces/dashboard/server.py`.
Its handlers, chat pipeline and shared `ConsoleState` live beside that module.

The React console lives in `apps/console`. `npm run build` produces
`apps/console/dist`; the gateway resolves checkout or packaged assets. The separate
assistant surface lives in `apps/assistant` and has its own dependency installation
and build. Electron and Capacitor shells live in `apps/desktop` and `apps/mobile`.
Their presence in the tree does not establish platform release availability.

Backend Python changes require a gateway restart. Console development uses Vite
on port 3100, proxying the gateway on `GIDEON_PORT` (10000 by default). Installed
app source is copied into the chosen home's `apps/<name>` directory, so changing
an external source checkout does not directly change its installed copy.

## Conversations and execution

`runtime/gideon/engine/session.py` owns runtime acquisition and conversation queues.
The native agent implementation is under `engine/agents/native`; ACP adapters are
under `integrations/acp`. Conversation history and context assembly live under
`cognition`. The dashboard chat pipeline handles accepted turns, streaming, replay,
approvals and persistence.

Session privacy and work authority are separate. Temporary work suppresses persistent
memory reads and writes; Incognito permits authorized reads while suppressing writes.
`runtime/gideon/security/session_credentials.py` binds actual work origins and execution
lineage. A caller-supplied session key, app label or parent identifier does not establish
that authority. App-origin work also carries a live capability ceiling; descendants
cannot widen it.

See [Chat and sessions](CHAT_SESSIONS.md), [Loops](LOOPS.md),
[Workflows](WORKFLOWS.md) and [Tasks and triggers](TASKS_TRIGGERS.md).

## Context and memory

The ordinary context implementation is `runtime/gideon/cognition/context.py`.
Hypermid integration lives under `runtime/gideon/hypermid`, with a Rust daemon built
from this repository's Cargo workspace. `hypermid/config.py` defines `off`,
`pass_through`, `shadow` and `primary` context modes; the default context configuration
is `off`. `hypermid/lifecycle.py` manages the configured integration, and
`hypermid/primary_engine.py` implements primary context assembly.

Hypermid's memory adapter, private-work scopes and app scopes are distinct native
consumers. Availability and permitted reads depend on configuration, the live service,
work origin and scope grants. A private workflow requires a live native private-work
receipt; changing its mode label does not supply one. Knowledge documents are a separate
library under `runtime/gideon/cognition/knowledge`.

See [Knowledge and memory](KNOWLEDGE_MEMORY.md) for the subsystem discussion. Its
configuration and authorization must be considered alongside the selected provider.

## Extension boundary

Provider contracts and loaders live under `runtime/gideon/extensions/providers`;
app manifests, installation and lifecycle live under `extensions/apps`. Bundled
apps ship under `extensions/apps/native`, and additional catalog or local sources
are configured by the operator. Model, channel, search, speech, agent and tool
integrations register through these seams.

The app import surface is **`gideon.sdk`**, implemented in `runtime/gideon/sdk`.
`packages/python-client` is a separate HTTP client for the gateway. Contributed
console pages use `@gideon/app-sdk`, implemented by
`apps/console/src/app/shell/appSdk.tsx`. Provider-specific behavior belongs in its
bundle; core supplies capability contracts and coordination.

App review covers permissions and declared execution paths. API access, work tiers,
MCP/tool grants and memory scopes are separate controls. In-process Python and host-tree
UI integrations are not isolation boundaries. See [App platform](APP_PLATFORM.md),
[Provider boundary](PROVIDER_BOUNDARY.md) and [Security limits](../security/LIMITATIONS.md).

## Source map

| Area | Source |
|---|---|
| Configuration and shared persistence | `runtime/gideon/core` |
| Agent sessions, delegation and lifecycle | `runtime/gideon/engine` |
| Context, history and knowledge | `runtime/gideon/cognition` |
| Native context and memory service integration | `runtime/gideon/hypermid`, `crates/` |
| Triggers, schedules and workflow execution | `runtime/gideon/automation` |
| Authentication, work credentials and guardrails | `runtime/gideon/security` |
| App lifecycle and extension registries | `runtime/gideon/extensions` |
| Protocol and external-service adapters | `runtime/gideon/integrations` |
| CLI, gateway API and console events | `runtime/gideon/interfaces` |
| App-facing Python API | `runtime/gideon/sdk` |
| Updates, snapshots, diagnostics and assurance | `runtime/gideon/operations`, `runtime/gideon/assurance` |

## State and configuration

`GIDEON_HOME` selects the state directory (default `~/.gideon`). Core configuration
is `config.json`, described by `runtime/gideon/core/config/loader.py`. Model bindings
live in `active_models.json`; search bindings, entity settings, installed app metadata,
provider settings and credentials have their own stores. Editing one store does not
necessarily update another. Dashboard writes may require the revision returned with
the original read; a stale draft must not be paired with a newer revision.

Updates depend on installation type. The update handlers and
`runtime/gideon/operations/self_update.py` coordinate supported git/package updates and recovery
state; container and desktop distribution remain deployment-specific. An app update
has its own review and lifecycle, separate from a core update.

Architecture describes implemented source paths, not a certification of every integration,
platform or deployment. Live provider credentials and platform capabilities still need
configuration and checks appropriate to their actual use.

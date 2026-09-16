<p align="left"><img src="docs/brand/gideon-mark.svg" width="72" height="72" alt="Gideon"></p>

# Gideon

Gideon brings a persistent personal agent, its tools, and its connected applications into one runtime. This repository contains the Python gateway, a React console, desktop and mobile shells, and a Python client SDK.

The source covers conversations and delegated work, autonomous goals, memory and knowledge, scheduling and workflows, channels, voice, browser and computer interaction, provider extensions, permissions, evaluation, and recovery. Integrations need their own configuration, credentials, and supported platform. Their presence in the tree does not establish that every capability has been exercised successfully.

## Run from a checkout

Use Python 3.12 or newer, Node.js 22.12 or newer, and npm. Run these commands from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Open the local console address printed by the gateway. The build writes the console to `apps/console/dist`, which the gateway can serve directly from this checkout. Follow setup and the console's provider settings to configure a model before trying a model-backed conversation.

`GIDEON_HOME` selects the directory containing configuration, credentials, conversations, and other runtime state. Without it, Gideon uses `~/.gideon`. Keep the isolated `.dev-home` setting when experimenting with this checkout. Stop the foreground gateway with Ctrl-C.

These commands describe the source setup; native application packaging and external provider connections have separate requirements. No public release repository or hosted endpoint is assumed. Release checking is configured through `GIDEON_RELEASE_REPOSITORY` when an actual repository has been designated.

## Repository map

| Path | Contents |
| --- | --- |
| `runtime/gideon/core` | Configuration, shared resources, and persistence helpers |
| `runtime/gideon/engine` | Agent execution and runtime coordination |
| `runtime/gideon/cognition` | Memory, knowledge, and context assembly |
| `runtime/gideon/automation` | Schedules, triggers, and workflows |
| `runtime/gideon/security` | Permissions, credential handling, and security controls |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | Provider and application integration |
| `runtime/gideon/interfaces` | CLI, gateway, and console API surfaces |
| `apps/console` | React application and shared client assets |
| `apps/desktop`, `apps/mobile` | Electron and Capacitor shells |
| `packages/python-client` | Python gateway client |
| `checks/runtime`, `checks/harness` | Runtime behavior checks and evaluation harness |
| `tooling`, `infrastructure` | Development utilities and deployment definitions |
| `spec/system-rewrite` | Current implementation scope and qualification records |

The application SDK lives in `runtime/gideon/sdk` and is imported as `gideon.sdk`. The Python client is a separate package under `packages/python-client`.

## Development status

The system rewrite is in progress. Several runtime and client mechanisms have been reimplemented and checked locally; substantial feature source remains present across the system. Focused passing checks establish only the behavior they cover. Complete independent reimplementation, full behavioral parity, external provider operation, and native release qualification are not established by this checkout.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development commands, [SECURITY.md](SECURITY.md) for handling security reports and runtime access, and the [active specification](spec/system-rewrite/spec.kvx) for task scope. Licensing and attribution are recorded in [LICENSE](LICENSE).

<p align="left"><img src="assets/gideon.png" alt="Gideon"></p>

<h1 align="center">Gideon Agent</h1>
<p align="center">
  <a href="https://github.com/Sidiora-Labs/centra-gideon-agent"><img src="https://img.shields.io/badge/Project-Centra%20AI-0A0A0A?style=flat-square" alt="Centra AI" /></a>
  <a href="https://github.com/Sidiora-Labs"><img src="https://img.shields.io/badge/Built%20by-Sidiora%20Labs-0A0A0A?style=flat-square" alt="Built by Sidiora Labs" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/LICENSE-2-0A0A0A?style=flat-square" alt="Apache License Version 2.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Version-0.1.3-0A0A0A?style=flat-square" alt="Version 0.1.3" /></a>
</p>
Gideon is a personal AI agent platform. This repository is its open-source, self-hosted edition: a gateway and web console for chat, long-running goal loops, memory, a knowledge base, tasks, schedules, an inbox, and a permission-gated app platform.

Gideon also offers a hosted service, its primary managed offering. The instructions here are for people who want to self-host or develop Gideon. You control your state directory and model providers, including API providers, OpenAI-compatible endpoints, AWS Bedrock and local models.

> **Open-source runtime:** The Python package in this checkout declares **v0.1.3**. For self-hosted upgrades, run `gideon snapshot` first and read [CHANGELOG.md](CHANGELOG.md). Hosted service releases are managed separately.

## What it does

- **Talk to it, or hand work off.** Chat sessions with streaming replies, tool calls, artifacts and a transcript you can search. Subagents take a job and report back while you keep working.
- **Let it run unattended.** Loops work a goal across many turns on a schedule. Tasks, triggers and workflows turn one-off requests into something repeatable.
- **Give it a memory.** Layered memory keeps preferences and context between conversations. The knowledge base holds the documents you point it at, so answers cite your material rather than the open internet.
- **Stay in the loop.** An inbox collects what needs you, from channels such as Slack as well as from Gideon itself. Voice input and spoken replies are optional extras.
- **Extend it.** The app platform and its Python SDK (`gideon.sdk`) cover models, channels, search, tools and dashboards. Skills, prompts and MCP servers add capability without touching the core.
- **Decide what it may touch.** Tool approvals, per-app permissions, credential handling, command screening and an audit trail. The core is provider-agnostic: integrations live in apps, never in the core package.
- **Watch it work.** The console shows sessions, activity, running loops, scheduled jobs and health, and a terminal for the machine Gideon is working on.

## Self-hosting requirements

- Python 3.12 or newer.
- Rust and Cargo for building the packaged Hypermid daemon from source. The checkout pins Rust 1.91.1 in `rust-toolchain.toml`. A supplied platform-specific wheel already contains the built daemon.
- Node.js 22.12 or newer with npm, if you want to build the console from source. CI builds the console with Node 24.
- macOS or Linux. On Windows, use the Docker Compose path in [docs/guides/CONTAINERS.md](docs/guides/CONTAINERS.md).
- A model provider for anything model-backed, configured after first start. A local model works too.

No external database server or message broker is required. The gateway can launch app backends, agent programs and the local Hypermid daemon; provider integrations may depend on services you configure.

## Self-host or develop from a checkout

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npm run build

export GIDEON_HOME="$PWD/.dev-home"
.venv/bin/gideon setup
.venv/bin/gideon gateway --no-open --port 10000
```

Open the console address the gateway prints. `npm run build` writes the console to `apps/console/dist`, which the gateway serves straight from the checkout. `gideon setup` asks for a workspace directory and a timezone. Model providers are configured afterwards, in the console, because a fresh home has no provider app to hold a credential yet.

`GIDEON_HOME` is the directory holding configuration, credentials, conversations and other runtime state. Without it, Gideon uses `~/.gideon`. Keeping the isolated `.dev-home` value while you experiment is deliberate: it keeps a development instance away from your real one. Stop the foreground gateway with Ctrl-C.

For a packaged install instead, `sh infrastructure/website/install.sh` bootstraps with `uv`. To check those installer bytes before you run them, see [Verify the one-liner](docs/guides/GETTING_STARTED.md#verify-the-one-liner). The full walkthrough, from nothing installed to a first chat, is in [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md).

For one-container Docker from this checkout, build with `docker build -f infrastructure/docker/Dockerfile.backend --target single -t gideon:local .`, then run `docker run -d --name gideon --restart unless-stopped -p 127.0.0.1:10000:10000 -v gideon_home:/data gideon:local`. The default workspace and state share the persistent volume; `docker logs gideon` prints the access URL.

## Develop

```sh
.venv/bin/python -m pip install -e '.[test]'
sh tooling/scripts/install_git_hooks.sh
```

The hook script formats staged Python and signs your commits off, which is what CI checks. `npm install` does not install hooks.

| Command | What it does |
| --- | --- |
| `make format` | Format Python with black and isort |
| `make lint` | black, isort, flake8 and mypy over the runtime and checks |
| `make test` | Run the Python suite (`checks/runtime`) |
| `make serve` | Start a gateway against `.dev-home` using the existing console build |
| `make serve-fresh` | Build console and assistant assets, then start the development gateway |
| `make serve-web` | Run the console dev server on port 3100 against a gateway |
| `npm run typecheck:web` | Type-check the console |
| `npm run test:web` | Console tests with Vitest |
| `make test-e2e` | Chromium interaction checks |
| `make docker-up` | Start the container stack from `infrastructure/compose` |

[CONTRIBUTING.md](CONTRIBUTING.md) covers the working agreement, the DCO sign-off, and how changes are classed and reviewed.

## Repository map

| Path | Contents |
| --- | --- |
| `runtime/gideon/core` | Configuration, shared resources, persistence helpers |
| `runtime/gideon/engine` | Agent execution and runtime coordination |
| `runtime/gideon/cognition` | History, knowledge and context assembly |
| `runtime/gideon/hypermid`, `crates/` | Native context and memory service, scope authority and Rust daemon |
| `runtime/gideon/automation` | Schedules, triggers and workflows |
| `runtime/gideon/security` | Permissions, credential handling and screening |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | Provider and application integration |
| `runtime/gideon/interfaces` | CLI, gateway and console API surfaces |
| `runtime/gideon/sdk` | The app SDK, imported as `gideon.sdk` |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | Self-update, backup and verification |
| `apps/console` | React console and shared client assets |
| `apps/assistant` | Separate assistant surface and web asset build |
| `apps/desktop`, `apps/mobile` | Electron and Capacitor shells |
| `packages/python-client` | Python client for the gateway API |
| `checks/runtime`, `checks/harness` | Behaviour checks and the self-development harness |
| `docs` | Architecture, guides, reference, security and design |
| `tooling`, `infrastructure` | Development scripts, packaging, containers, website |
| `examples` | An app template and a registry example, neither served nor installed |

## Documentation

[docs/README.md](docs/README.md) is the index. The short path in:

- [Console product guide](apps/console/PRODUCT.md) for the interface and its destinations.
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) for how the gateway is put together.
- [docs/reference/CLI.md](docs/reference/CLI.md) for every command and flag.
- [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) for every setting.
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) for what the trust boundaries actually are.

## Status

Gideon is available as a hosted service and continues to develop alongside its open-source runtime. This repository documents the runtime, console, desktop and mobile shells, Python client, and self-hosting and development workflows. Its package versions and CI results describe this source tree; hosted releases have their own lifecycle.

CI covers Python, console, bundled-app and interaction checks according to the workflow event and job configuration. See [GitHub Actions](https://github.com/Sidiora-Labs/centra-gideon-agent/actions) for results on a specific revision and [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) for published repository artifacts.

## Security

Gideon reads local files, runs tools and talks to services you configure, so the gateway token and the account it runs under grant real access. Report vulnerabilities through the private reporting route in [SECURITY.md](SECURITY.md).

Model requests, channel delivery, downloads, catalog access and updates can contact configured external services. Review the provider settings and [network egress inventory](docs/architecture/NETWORK_EGRESS_HOSTS.txt) before connecting an integration.

## Contributing and getting help

- [CONTRIBUTING.md](CONTRIBUTING.md) for setup, commands, the DCO sign-off and review.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for how we treat each other here.
- [SUPPORT.md](SUPPORT.md) for where to ask, and what to include when you do.
- [GOVERNANCE.md](GOVERNANCE.md) for who decides what.
- [Issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues) for bugs and ideas, [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) for what shipped, and the [security policy](https://github.com/Sidiora-Labs/centra-gideon-agent/security/policy) for how vulnerabilities are handled. The repository itself lives at [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent).

## License

Apache License 2.0. See [LICENSE](LICENSE). Copyright 2026 Sidiora Labs Inc.

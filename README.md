<p align="left"><img src="docs/brand/gideon-mark.svg" width="72" height="72" alt="Gideon"></p>

# Gideon

Gideon is a personal AI agent that runs on your own machine. One process serves a web console and does the work: chat, long-running goal loops, memory, a knowledge base, tasks, schedules, an inbox, and a permission-gated app platform.

It is built for one person who wants an agent with real access to their own computer and their own services, without handing the keys to a hosted product. State lives in a directory you choose. Model providers are pluggable: an Anthropic or OpenAI key, an OpenAI-compatible endpoint, AWS Bedrock credentials, or a model running locally.

> **Pre-1.0:** Gideon is at **v0.1.3**. It moves quickly and a release can break something. Run `gideon snapshot` before you upgrade, and read [CHANGELOG.md](CHANGELOG.md) for what shipped.

## What it does

- **Talk to it, or hand work off.** Chat sessions with streaming replies, tool calls, artifacts and a transcript you can search. Subagents take a job and report back while you keep working.
- **Let it run unattended.** Loops work a goal across many turns on a schedule. Tasks, triggers and workflows turn one-off requests into something repeatable.
- **Give it a memory.** Layered memory keeps preferences and context between conversations. The knowledge base holds the documents you point it at, so answers cite your material rather than the open internet.
- **Stay in the loop.** An inbox collects what needs you, from channels such as Slack as well as from Gideon itself. Voice input and spoken replies are optional extras.
- **Extend it.** The app platform and its Python SDK (`gideon.sdk`) cover models, channels, search, tools and dashboards. Skills, prompts and MCP servers add capability without touching the core.
- **Decide what it may touch.** Tool approvals, per-app permissions, credential handling, command screening and an audit trail. The core is provider-agnostic: integrations live in apps, never in the core package.
- **Watch it work.** The console shows sessions, activity, running loops, scheduled jobs and health, and a terminal for the machine Gideon is working on.

## Requirements

- Python 3.12 or newer.
- Node.js 22.12 or newer with npm, if you want to build the console from source. CI builds the console with Node 24.
- macOS or Linux. On Windows, use the Docker Compose path in [docs/guides/containers.md](docs/guides/containers.md).
- A model provider for anything model-backed, configured after first start. A local model works too.

There is no external database and no message broker. Everything runs in the one gateway process.

## Run from a checkout

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

For a packaged install instead, `sh infrastructure/website/install.sh` bootstraps with `uv`. To check those installer bytes before you run them, see [Verify the one-liner](docs/guides/getting-started.md#verify-the-one-liner). The full walkthrough, from nothing installed to a first chat, is in [docs/guides/getting-started.md](docs/guides/getting-started.md).

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
| `make serve` | Build the console and start a gateway against `.dev-home` |
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
| `runtime/gideon/cognition` | Memory, knowledge and context assembly |
| `runtime/gideon/automation` | Schedules, triggers and workflows |
| `runtime/gideon/security` | Permissions, credential handling and screening |
| `runtime/gideon/integrations`, `runtime/gideon/extensions` | Provider and application integration |
| `runtime/gideon/interfaces` | CLI, gateway and console API surfaces |
| `runtime/gideon/sdk` | The app SDK, imported as `gideon.sdk` |
| `runtime/gideon/operations`, `runtime/gideon/assurance` | Self-update, backup and verification |
| `apps/console` | React console and shared client assets |
| `apps/desktop`, `apps/mobile` | Electron and Capacitor shells |
| `packages/python-client` | Python client for the gateway API |
| `checks/runtime`, `checks/harness` | Behaviour checks and the self-development harness |
| `docs` | Architecture, guides, reference, security and design |
| `tooling`, `infrastructure` | Development scripts, packaging, containers, website |
| `examples` | An app template and a registry example, neither served nor installed |

## Documentation

[docs/README.md](docs/README.md) is the index. The short path in:

- [docs/vision.md](docs/vision.md) for what this is trying to be.
- [docs/architecture/overview.md](docs/architecture/overview.md) for how the gateway is put together.
- [docs/reference/cli.md](docs/reference/cli.md) for every command and flag.
- [docs/reference/CONFIG-REFERENCE.md](docs/reference/CONFIG-REFERENCE.md) for every setting.
- [docs/security/threat-model.md](docs/security/threat-model.md) for what the trust boundaries actually are.

## Status

Gideon is pre-1.0 and under active development. The gateway, console, desktop and mobile shells, the Python client, and the checks that exercise them are all in the tree, and CI runs the Python suite, the console tests and the interaction checks on every change.

What that does not mean: no hosted service exists, and this repository assumes neither a published package nor a release endpoint unless you point `GIDEON_RELEASE_REPOSITORY` at one. Integrations need their own configuration, credentials and platform support. Some capabilities in the tree have not been exercised end to end against a live provider. Passing checks say something about what they cover and nothing about the rest.

## Security

Gideon reads local files, runs tools and talks to services you configure, so the gateway token and the account it runs under grant real access. Reports go through a private channel to whoever gave you the checkout. See [SECURITY.md](SECURITY.md).

Gideon sends no telemetry. Nothing about your usage leaves your machine unless you configure an integration that does.

## Contributing and getting help

- [CONTRIBUTING.md](CONTRIBUTING.md) for setup, commands, the DCO sign-off and review.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for how we treat each other here.
- [SUPPORT.md](SUPPORT.md) for where to ask, and what to include when you do.
- [GOVERNANCE.md](GOVERNANCE.md) for who decides what.
- [Issues](https://github.com/sidiora-labs/centra-gideon-agent/issues) for bugs and ideas, [releases](https://github.com/sidiora-labs/centra-gideon-agent/releases) for what shipped, and the [security policy](https://github.com/sidiora-labs/centra-gideon-agent/security/policy) for how vulnerabilities are handled. The repository itself lives at [sidiora-labs/centra-gideon-agent](https://github.com/sidiora-labs/centra-gideon-agent).

## License

Apache License 2.0. See [LICENSE](LICENSE). Copyright 2026 Sidiora Labs Inc.

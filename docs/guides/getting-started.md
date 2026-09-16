# Getting started

Gideon is a self-hosted personal AI agent: a local gateway process that
serves a web dashboard, runs agents with tooling/memory/skills, and connects to
channels. This guide takes you from **nothing installed** to your first chat.

> **Pre-1.0:** Gideon is pre-1.0 and moves fast; releases may make
> breaking changes. Run `gideon snapshot` before upgrading.

## Prerequisites

- macOS or Linux (Windows: use the Docker Compose path below)
- An API key for at least one model provider (Anthropic, OpenAI, an
  OpenAI-compatible endpoint, AWS Bedrock credentials, or a local Ollama —
  anything from the Store's model-provider apps)

For a source checkout, install Python 3.12+ and Node 20+, then build the
console before packaging the runtime. A wheel built from this tree includes the
console assets and can run without Node on the destination machine.

## 1. Install

From the repository root:

```bash
npm ci
npm run build
python3 -m pip install .
gideon setup
```

The bootstrap path uses `uv` and accepts the current checkout by default:

```bash
sh infrastructure/website/install.sh
```

For a separately supplied wheel or release source, set `GIDEON_PACKAGE_SOURCE`
to its explicit path, URL, or versioned distribution specification before running
the installer. This project does not assume that a Gideon package, image, or hosted
installer has already been published.

`setup` configures the workspace directory and timezone. Provider credentials
are configured after starting the gateway, as described below. Run setup on an
interactive terminal so its prompts can collect your choices.

### Verify the installer

```bash
cd infrastructure/website
sha256sum -c install.sh.sha256
sh install.sh
```

The digest checks these installer bytes. A published installer should have its
digest distributed through an independent trusted release channel. The installer
may fetch Astral's `uv` installer over TLS if uv is unavailable; that dependency
fetch is not covered by this file's digest.

### Optional extras

The base install is lean. Add an extra only if you need what it unlocks (most
users install provider **apps** from the Store instead — the app pulls its own
dependency; extras are the plain-pip path):

| Extra | Install | Unlocks | Weight |
|---|---|---|---|
| `openai` | `pip install '.[openai]'` | the OpenAI SDK (chat/embeddings/STT/TTS) | small |
| `anthropic` | `pip install '.[anthropic]'` | the Anthropic SDK | small |
| `bedrock` | `pip install '.[bedrock]'` | AWS Bedrock (`boto3`) | medium |
| `mcp` | `pip install '.[mcp]'` | Model Context Protocol servers/tools | small |
| `js-render` | `pip install '.[js-render]'` | JS-rendered web fetch (Playwright) | large (browser) |
| `models` | `pip install '.[models]'` | local inference: embeddings + STT + TTS | large (ML) |

> With `uv tool`, add an extra with `uv tool install '.[bedrock]'`.
> `gideon doctor` reports which optional dependencies are missing and
> prints the exact command to add them.

## 2. First run

```bash
gideon gateway
```

The gateway binds to port **10000** by default (`--port` or `GIDEON_PORT`
to change) and opens the dashboard in your browser (`--no-open` to skip). All
state lives under `~/.gideon/` (relocatable with `GIDEON_HOME`).

If you need the URL again later — it is auth-gated — run `gideon token`,
which prints a ready-to-open URL with a fresh credential.

First-run onboarding in the dashboard asks for your name and walks you to
provider setup.

## 3. Configure a model provider

Model providers are installable apps — nothing is hardwired to a vendor.

1. Open **Apps** (the Store) in the dashboard sidebar.
2. Install the provider app for your vendor (e.g. *Anthropic Models*,
   *OpenAI Models*, *Bedrock Models*, *Ollama Models*, or *OpenAI-compatible*
   for any compatible endpoint). The app installs its own SDK dependency.
3. The provider appears under **Settings → Providers** — add your API key /
   endpoint there and hit **Test** to verify connectivity.
4. Go to **Settings → Models** and bind a model to the **chat** use case
   (bindings live in `~/.gideon/active_models.json`, not `config.json`).
   The same panel binds models for background work, embeddings, ingestion,
   speech, and more — they can all be different providers.

Prefer the terminal? Once a provider app is installed,
`gideon setup --provider NAME --credential NAME=VALUE` stores the
credential without the dashboard, and `gideon doctor` verifies the result
end to end.

## 4. First chat

Open the dashboard's **Chat** page and send a message — or from the terminal:

```bash
gideon chat -m "hello"
```

Tool calls the agent wants to make appear as approval prompts (default
`agent.approval_mode: auto`; see the
[configuration reference](../reference/configuration.md) to tune approval,
sandboxing, and security policy).

## Docker Compose

Run a published release without installing anything but Docker. From a checkout
(or after downloading `infrastructure/compose/compose.yaml`):

```bash
cp .env.example .env         # set provider keys / options
docker compose -f infrastructure/compose/compose.yaml up -d
```

The gateway comes up on `http://127.0.0.1:10000` with a persistent
`gideon_home` volume and a healthcheck. Pin a release with
`GIDEON_IMAGE_TAG` in `.env`. See the
[container guide](containers.md) for ports, volumes, backups, and updates.

## Where to go next

- **Explore the platform** — Skills, Agents, Tasks, goal Loops, Knowledge,
  Memory, Inbox, Triggers, and Workflows all live in the sidebar; each page has
  inline explanations.
- **Install more apps** — search providers, speech (STT/TTS), local models,
  channel connectors, and agent runtimes are all Store apps.
- **Run it permanently** — `gideon service install` registers a systemd
  unit (Linux) or launchd agent (macOS) so the gateway survives reboots.
- **Back it up** — `gideon snapshot` creates a portable state archive;
  `gideon restore` brings it back.

## Reference docs

- [Configuration reference](../reference/configuration.md) — every config field,
  its default, and where to set it.
- [CLI reference](../reference/cli.md) — every command and flag.
- [API overview](../reference/api-overview.md) — the full REST/WS surface.
- Roadmap — where the project is heading.

## Troubleshooting

- **"Gateway not running" from CLI commands** — `status`/`stop`/`token` need a
  live gateway on the resolved port; pass `--port` if you changed it.
- **Backend code changes don't take effect** (source checkouts) — Python
  changes need a gateway restart (`gideon restart`); only frontend
  rebuilds are live.
- **Model errors in chat** — check **Settings → Models** has a chat binding and
  the provider's **Test** passes; `gideon doctor` reports the live
  binding and any missing optional dependency with the exact install command.
- **Dashboard shows nothing / 404 assets** (source checkouts only) — the SPA
  isn't built: run `make web-build`, then restart the gateway. Wheel, uv, pipx,
  and Docker installs ship the prebuilt dashboard, so this never applies to them.

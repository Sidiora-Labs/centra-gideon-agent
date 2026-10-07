# Contributing to Gideon

Gideon is a personal AI agent that runs on your own machine. The repository is [Sidiora-Labs/centra-gideon-agent](https://github.com/Sidiora-Labs/centra-gideon-agent), licensed under Apache 2.0. See [LICENSE](LICENSE).

Two things decide whether a change lands: it does something real for a user, and you ran the check that covers it.

## Set up your environment

You need Python 3.12 or newer, and Node.js 22.12 or newer with npm for console work. CI builds the console with Node 24. Building a runtime wheel from source also builds the Rust Hypermid daemon; install the pinned Rust 1.91.1 toolchain through rustup, or supply the platform-specific prebuilt daemon through `GIDEON_PREBUILT_HYPERMID_DAEMON`. See [Getting started](docs/guides/GETTING_STARTED.md).

With a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

Or with uv, which is what CI uses: `uv sync --locked --extra dev`.

Keep npm installs at the repository root. The root `package-lock.json` covers console, desktop and mobile workspaces. The assistant surface uses its own `apps/assistant/package-lock.json` and `npm --prefix apps/assistant ci`. `uv.lock` pins the Python environment used by the locked setup path.

## Install the git hooks

Run `sh tooling/scripts/install_git_hooks.sh` once per clone. It points `core.hooksPath` at `tooling/hooks`. Those hooks format your staged Python with black and isort, then add the `Signed-off-by` trailer to the commit message.

`npm install` does not install them. Only that script does.

## Run it

`make serve` starts the gateway on port 10000 against `.dev-home` in your checkout. `make serve` uses an existing console build. On a fresh clone, `make serve-fresh` installs and builds both console and assistant assets first.

To set up and start it by hand:

```sh
GIDEON_HOME="$PWD/.dev-home" .venv/bin/gideon setup
GIDEON_HOME="$PWD/.dev-home" .venv/bin/gideon gateway --no-open --port 10000
```

For console work, leave the gateway running and start `make serve-web` in a second terminal. Vite listens on port 3100 and proxies to the gateway on 10000, so you get hot reload without a rebuild. `npm run build` writes the bundled console to `apps/console/dist`. Restart the gateway after you change Python code.

## Verify your change

Write the whole change first. Then run the one command below that covers it.

| Command | What it does |
| --- | --- |
| `make format` | Format Python with black and isort |
| `make lint` | `black --check`, `isort --check-only`, `flake8`, `mypy` |
| `make test` | pytest over `checks/runtime` |
| `npm run typecheck:web` | Type check the console |
| `npm run test:web -- <path>` | Run the console tests you name |
| `npm run build` | Type check and build the console |
| `make test-e2e` | Chromium interaction checks |
| `make test-visual` | Chromium visual snapshot checks |
| `make gates` | All configured maintenance gates |

Run it once. If it fails, fix the cause and run it again. We do not re-run a check that already passed.

Tests that write state use a temporary directory or an isolated `GIDEON_HOME`, never real credentials, tokens or conversations. See [SECURITY.md](SECURITY.md).

## Changelog formatting

The formatter previews output by default and does not replace the changelog:

```sh
python3 tooling/scripts/format_changelog.py --output /tmp/gideon-changelog-preview.md
python3 tooling/scripts/format_changelog.py --check
```

Writing requires `--write --backup <path>` with a new backup path outside the
Git worktree. Use `--expected-sha256 <reviewed-hash>` to refuse an unexpected
source change. Review the preview first. The formatter preserves release
sections and existing prose; formatting is not evidence of a published release.

## Breaking changes

Gideon is pre-1.0, so we can still change things. Class your change before you open a pull request.

- **R (reversible):** nothing persisted and no stable surface changes.
- **B (behavioral):** changes a stable surface (API, CLI, config) or persisted state.
- **S (schema):** changes a stored schema or another stable contract.

Aim for R. If your change is B or S, describe the break in the pull request and add a CHANGELOG entry. Document persisted-state and API compatibility implications explicitly. Existing migration and portability paths must be evaluated for the specific store; do not assume a universal automatic migration or add a compatibility layer without agreeing its scope. The maintainer decides whether to take the break, reshape it, or schedule it.

## Developer Certificate of Origin (DCO)

```sh
git rebase --signoff main
git push --force-with-lease
```

CI checks this commit by commit on every pull request ([.github/workflows/dco.yml](.github/workflows/dco.yml)). Dependabot pull requests are exempt.

## Where to read more

- [docs/README.md](docs/README.md) for the index of everything else.
- [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) for how the gateway is put together.
- [docs/reference/CLI.md](docs/reference/CLI.md) for every command and flag, and [docs/reference/CONFIGURATION_REFERENCE.md](docs/reference/CONFIGURATION_REFERENCE.md) for every setting.
- [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md) for the trust boundaries.
- [.github/workflows/ci.yml](.github/workflows/ci.yml) for the exact checks CI runs.
- [CHANGELOG.md](CHANGELOG.md) for what shipped in each release.

Bugs and ideas go to [issues](https://github.com/Sidiora-Labs/centra-gideon-agent/issues), code goes to [pull requests](https://github.com/Sidiora-Labs/centra-gideon-agent/pulls), and [releases](https://github.com/Sidiora-Labs/centra-gideon-agent/releases) list what shipped. Read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before you post, and report vulnerabilities through [SECURITY.md](SECURITY.md) rather than a public issue.

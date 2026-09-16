# Developing Gideon

Work from the repository root. The Python distribution is `gideon-agent-harness`, with source under `runtime/gideon`; the console, desktop shell, and mobile shell are npm workspaces under `apps/`. Read [AGENTS.md](AGENTS.md) and the active specification under `spec/` before changing a task. The user's requested scope determines what work is authorized.

## Local environment

Use Python 3.12 or newer and Node.js 22.12 or newer with npm:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
npm ci
```

The `test` extra installs the Python test dependencies. Optional provider, voice, embedding, and development dependencies are declared in [pyproject.toml](pyproject.toml); install the extras needed for the change. The broader `dev` extra also includes local model dependencies, so it is larger than the focused test setup.

Keep npm installation at the root, where `package-lock.json` covers all three workspaces. Commands listed below are defined in the current root or workspace `package.json` files. Do not assume that package installation installs repository hooks.

## Run the gateway and console

Build the console and start a gateway with an isolated state directory:

```sh
npm run build
GIDEON_HOME="$PWD/.dev-home" .venv/bin/gideon setup
GIDEON_HOME="$PWD/.dev-home" .venv/bin/gideon gateway --no-open --port 10000
```

For console development, leave the gateway running and run this in another terminal:

```sh
npm run dev
```

Vite starts on port `3100` and proxies API and application requests to the gateway on `127.0.0.1:10000`. If the gateway uses a different port, pass it to Vite with `GIDEON_PORT=10001 npm run dev`. Restart the gateway after changing Python code. The production console output is `apps/console/dist`.

Desktop and mobile startup and packaging are separate from the console build. Inspect [apps/desktop/package.json](apps/desktop/package.json) and [apps/mobile/package.json](apps/mobile/package.json) for their commands and [apps/mobile](apps/mobile) for native project configuration. A console build does not establish that a packaged native application works on its target operating system.

## Implement and qualify a change

Preserve observable contracts when rewriting implementation: API routes, application SDK exports, stored formats, credential permissions, concurrency behavior, retention limits, and cancellation behavior. Replace a mechanism completely within the agreed scope and migrate its consumers. Keep fixtures that capture existing behavior, and prefer checks that exercise real code and persisted results over assertions tied to source spelling.

Finish the task implementation before running its declared checks. Follow the active task's gate limits; select the relevant command instead of treating the list below as a mandatory combined suite.

| Command | Scope |
| --- | --- |
| `.venv/bin/python -m pytest checks/runtime/test_shutdown_event.py -q` | Focused Python behavior example; substitute the task's declared files |
| `npm run typecheck:web` | Console TypeScript checks |
| `npm run test:web -- <test-path>` | Selected console tests, with the path interpreted by the console workspace |
| `npm run build` | Console type check and production asset build |
| `npm run test:desktop` | Desktop shell tests |
| `npm run test:mobile` | Mobile shell tests |
| `npm run smoke:render` | Browser rendering check for an authorized integration or release gate |

Python formatting settings are in `pyproject.toml`. Follow the surrounding module's conventions and format only touched files. Browser, aggregate, platform, and provider checks belong to the relevant integration or release scope. A focused test result is not a production certification.

Tests that write state must use temporary directories or an explicitly isolated `GIDEON_HOME`. Never put provider credentials, gateway access tokens, or captured private conversations in fixtures or shared logs. See [SECURITY.md](SECURITY.md).

## Handoff and review

Describe the concrete behavior changed, the affected public or stored contracts, and the exact commands that ran. Record the revision, exit code, and log path for qualification. Separate implemented behavior from behavior exercised in a live user path, and state any failure or untested dependency directly.

The current task record is [spec/system-rewrite/spec.kvx](spec/system-rewrite/spec.kvx). Do not mark whole-system parity or independent reimplementation complete on the basis of a few passing checks. Keep unrelated edits out of a task and preserve required licensing and attribution in [LICENSE](LICENSE).

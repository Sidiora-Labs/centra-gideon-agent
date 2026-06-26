# Gideon Agent Reference

Offline API/tool reference for Gideon (manifest apiVersion 1). Generated from the live registries — the same source as `GET /api/manifest`. Load the `pclaw-api` skill for the driving methodology; this reference is the exact-signature lookup it points to.

## How to use this (orient, then drill)

1. Read this index to locate the surface you need — don't read every file.
2. Drill into the one relevant section:
   - **[tools.md](tools.md)** — 86 registered tools across 13 providers, with exact input schemas + examples.
   - **[routes.md](routes.md)** — 632 agent-callable HTTP routes (of 636 total), with summaries.
   - **[providers.md](providers.md)** — the provider-type taxonomy + 27 registered providers.
3. Copy the exact signature — never guess a parameter name.
4. After a mutating call, read the entity back to confirm it took.

## Tool providers at a glance

- `gideon-artifacts` — 13 tools
- `gideon-automation` — 9 tools
- `gideon-core` — 14 tools
- `gideon-inbox-tools` — 1 tools
- `gideon-knowledge-tools` — 5 tools
- `gideon-memory` — 4 tools
- `gideon-project-tools` — 4 tools
- `gideon-prompts` — 1 tools
- `gideon-subagents` — 3 tools
- `gideon-tasks-tools` — 9 tools
- `gideon-ui-docs` — 2 tools
- `gideon-workflows` — 19 tools
- `workflows-tools` — 2 tools

## Repo gotchas that keep resurfacing

These are environment invariants, not API facts — but they cost more driving turns than any signature:

- **Installed apps run from `$GIDEON_HOME/apps/<name>/`, not the workspace tree.** Push code edits with `POST /api/apps/{name}/update` `{source, confirm:true}` — editing the workspace clone does nothing to the running app.
- **`static/dist` is a SYMLINK to `web/dist`, not a copy.** A `cp -R` leaves a frozen dir that shadows it and serves a stale SPA. Rebuild the frontend in place; never replace the symlink with a copy.
- **Use the venv interpreter.** Run the gateway and tools through the project's `.venv` (`.venv/bin/gideon`), not a system Python that lacks the installed dependencies.
- **Locate this reference from the binary:** `gideon doctor --paths` prints the reference directory (and the config / skills / install dirs) so an external agent can find these files without knowing the install layout.

## Scope — what NOT to do

- Don't hand-roll UI when a tool or route already does the job; the manifest is the inventory of what already exists.
- Don't bypass `POST /api/apps/{name}/update` by editing an installed app's files directly.
- Don't call a route the manifest does not mark `agent_callable` as if it were an agent API.

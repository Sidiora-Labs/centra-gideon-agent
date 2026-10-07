# Gideon Agent Reference

Offline API/tool reference for Gideon (manifest apiVersion 1). Generated from bundled tool/provider declarations and static route registrations. `GET /api/manifest` describes the running gateway. Load the `gideon-api` skill for API usage; this reference is the exact-signature lookup it points to.

## How to use this (orient, then drill)

1. Read this index to locate the surface you need — don't read every file.
2. Drill into the one relevant section:
   - **[tools.md](tools.md)** — 672 bundled tools across 53 providers, with exact input schemas + examples.
   - **[routes.md](routes.md)** — 1152 agent-callable HTTP routes (of 1171 total), with summaries.
   - **[providers.md](providers.md)** — the provider-type taxonomy + 91 bundled provider declarations.
3. Copy the exact signature — never guess a parameter name.
4. After a mutating call, read the entity back to confirm it took.

## Tool providers at a glance

- `gideon-artifacts` — 13 tools
- `gideon-automation` — 11 tools
- `gideon-body-composition` — 2 tools
- `gideon-computer-use` — 7 tools
- `gideon-core` — 17 tools
- `gideon-creative` — 98 tools
- `gideon-creative-commissions` — 10 tools
- `gideon-creative-direction` — 2 tools
- `gideon-creative-exports` — 2 tools
- `gideon-experience` — 29 tools
- `gideon-game-assets` — 3 tools
- `gideon-identity` — 58 tools
- `gideon-inbox-tools` — 2 tools
- `gideon-integration-apps` — 3 tools
- `gideon-knowledge-tools` — 9 tools
- `gideon-lifestyle-profile` — 6 tools
- `gideon-media` — 48 tools
- `gideon-media-sharing` — 3 tools
- `gideon-memory` — 6 tools
- `gideon-moltbook` — 3 tools
- `gideon-moltworld` — 5 tools
- `gideon-music-assemblies` — 7 tools
- `gideon-music-decks` — 15 tools
- `gideon-music-generation` — 6 tools
- `gideon-music-listening` — 10 tools
- `gideon-music-midi` — 5 tools
- `gideon-music-models3d` — 8 tools
- `gideon-music-rounds` — 6 tools
- `gideon-music-tools` — 11 tools
- `gideon-music-video` — 8 tools
- `gideon-peers` — 1 tools
- `gideon-people` — 73 tools
- `gideon-personal-knowledge` — 50 tools
- `gideon-platform` — 23 tools
- `gideon-privacy-broker-beenverified` — 4 tools
- `gideon-privacy-broker-spokeo` — 4 tools
- `gideon-privacy-broker-whitepages` — 4 tools
- `gideon-project-tools` — 4 tools
- `gideon-prompts` — 1 tools
- `gideon-remote-media` — 3 tools
- `gideon-replication` — 1 tools
- `gideon-subagents` — 4 tools
- `gideon-tasks-tools` — 9 tools
- `gideon-ui-docs` — 3 tools
- `gideon-wellbeing` — 1 tools
- `gideon-wellbeing-epigenetic` — 6 tools
- `gideon-wellbeing-eyes` — 6 tools
- `gideon-workflows` — 20 tools
- `gideon-world-foundations` — 3 tools
- `outbound_email` — 5 tools
- `remote-agent-sessions` — 3 tools
- `workflows-tools` — 2 tools
- `workspace-tools` — 29 tools

## App updates and frontend assets

- `POST /api/apps/{name}/update` previews `{source}`. Submit the reviewed `{source, review_digest}` to apply it; a changed staged bundle requires another review. A `confirm` flag does not replace this digest.
- The console build is `apps/console/dist`. In a source checkout, `make web-build` links `runtime/gideon/static/dist` to that build. Packaged distributions include the assets at `gideon/static/dist`; they need not use a symlink.
- `gideon doctor --paths` reports the installed reference directory.

# Gideon documentation

The docs are split by the question you are asking. The architecture documents are the map of how the system is built. The guides are the how-to for getting a task done. The reference is the lookup for a command, a setting or a route. The security documents state the honest limits, including what is not enforced yet.

Every link below points at a file in this tree. Start with the two files at the top of the tree, then open the folder that matches your question: `architecture/`, `guides/`, `reference/`, `security/`, `design/`, `maintainers/` or `brand/`.

## Where to start

- [VISION.md](VISION.md): what this project is trying to be, the tenets a decision is reconciled against, and the stated non-goals.
- [README.md](README.md): this page, the index into every other document.

## `architecture/` (the map)

- [OVERVIEW.md](architecture/OVERVIEW.md): the shape of the gateway process and the subsystems inside it.
- [CHAT_SESSIONS.md](architecture/CHAT_SESSIONS.md): how a message becomes a turn, and where a session and its history live.
- [LOOPS.md](architecture/LOOPS.md): how an autonomous goal loop runs its cycles, who judges the result, and what ends it.
- [WORKFLOWS.md](architecture/WORKFLOWS.md): how a declarative workflow graph is scheduled, resumed, edited and rewound.
- [TASKS_TRIGGERS.md](architecture/TASKS_TRIGGERS.md): how projects, tasks and task lists work, and what fires a trigger or a schedule.
- [KNOWLEDGE_MEMORY.md](architecture/KNOWLEDGE_MEMORY.md): what separates the knowledge library from what the assistant remembers, and how each is searched.
- [INBOX_CHANNELS.md](architecture/INBOX_CHANNELS.md): how items arrive for your attention, and how a channel app carries messages in both directions.
- [APP_PLATFORM.md](architecture/APP_PLATFORM.md): what an app can be, and how it is installed, permissioned and run as a backend subprocess.
- [WIDGETS.md](architecture/WIDGETS.md): what an agent-authored widget may send to its host, and what the host accepts back.
- [AGENT_ACTIVITY_FEED.md](architecture/AGENT_ACTIVITY_FEED.md): what a dashboard agent world is handed as its read contract, and what it must not assume.
- [TOOL_NAMES.md](architecture/TOOL_NAMES.md): whether a tool call still lands on the tool it names after every hop.
- [PROVIDER_BOUNDARY.md](architecture/PROVIDER_BOUNDARY.md): why the core stays provider-agnostic, and where the line was drawn.
- [PROVIDER_BOUNDARY_KEEPS.txt](architecture/PROVIDER_BOUNDARY_KEEPS.txt): which vendor-shaped references are deliberate, with the reasoning for each.
- [SECURITY.md](architecture/SECURITY.md): how the internal controls layer fits together, from authentication to the audit log.
- [NETWORK_EGRESS_HOSTS.txt](architecture/NETWORK_EGRESS_HOSTS.txt): every host the shipped code can contact, and the condition that triggers it.
- [acp-parity.md](agents/acp-parity.md): what running a turn over the Agent Client Protocol costs against the native runtime, provider by provider.
- [WINDOWS_NATIVE_AUDIT.md](architecture/WINDOWS_NATIVE_AUDIT.md): what native Windows support would cost, and the go/no-go it feeds.

## `guides/` (the how-to)

- [GETTING_STARTED.md](guides/GETTING_STARTED.md): how to go from nothing installed to a first chat.
- [CHAT_SURFACE.md](guides/CHAT_SURFACE.md): what a conversation can do beyond sending a message.
- [DESKTOP.md](guides/DESKTOP.md): what the desktop app adds over a browser tab, and how each capability is granted.
- [COMPANION_APPS.md](guides/COMPANION_APPS.md): how a phone or a second computer finds this gateway and is allowed in.
- [REMOTE_ACCESS.md](guides/REMOTE_ACCESS.md): how to reach your own dashboard when you are away from home.
- [PLATFORMS.md](guides/PLATFORMS.md): which platforms are supported, on what evidence, including Windows through WSL2 or Docker Desktop.
- [CONTAINERS.md](guides/CONTAINERS.md): how to run the gateway with Docker Compose, including ports, volumes, backups and updates.
- [USE_FROM_YOUR_IDE.md](guides/USE_FROM_YOUR_IDE.md): how to let your editor's assistant ask this Gideon what it knows.
- [BUILD_A_CHANNEL_APP.md](guides/BUILD_A_CHANNEL_APP.md): how to write an app that carries messages to and from one chat service.
- [WORKFLOW_TEMPLATES.md](guides/WORKFLOW_TEMPLATES.md): how to write a workflow template that is readable and survives edits.

## `reference/` (the lookup)

- [CLI.md](reference/CLI.md): what every `gideon` command and flag does.
- [CONFIGURATION.md](reference/CONFIGURATION.md): where configuration lives, and which setting to change.
- [CONFIGURATION_REFERENCE.md](reference/CONFIGURATION_REFERENCE.md): which operator-only knobs have no dashboard control.
- [API_OVERVIEW.md](reference/API_OVERVIEW.md): which REST and WebSocket routes the gateway serves.
- [SKILL_FORMAT.md](reference/SKILL_FORMAT.md): what the SKILL.md loader accepts, ignores and extends.
- [LEARNING_BENCHMARK_PROTOCOL.md](reference/LEARNING_BENCHMARK_PROTOCOL.md): how the claim that an approved skill improves the next run is measured, and what must exist before a number is published.

## `security/` (the honest limits)

- [THREAT_MODEL.md](security/THREAT_MODEL.md): the trust boundaries, the control that guards each one, and what is out of scope.
- [LIMITATIONS.md](security/LIMITATIONS.md): which claimed controls are not enforced yet at the point of execution.
- [REVIEW_SCOPE.md](security/REVIEW_SCOPE.md): the five paths an external reviewer is asked to attack, and how a finding is recorded.
- [SCANNER_TESTING.md](security/SCANNER_TESTING.md): the attack classes the community-content scanner refuses, and the tests that prove each rule carries weight.
- [SIGNING.md](security/SIGNING.md): how an artifact's provenance is decided, and what a signature buys.

## `design/`

- [PATTERNS.md](design/PATTERNS.md): which shared primitive or interaction pattern to use for a new surface.
- [MOTION.md](design/MOTION.md): which transition preset to pick, and what it is allowed to do.
- [CONSISTENCY_AUDIT.md](design/CONSISTENCY_AUDIT.md): where the shipped design system drifts from its authority, ranked worst first.
- [consistency-audit.json](design/consistency-audit.json): the same audit as machine-readable counts, regenerated by `npm run audit:consistency`.
- [CONSISTENCY_HANDOFF.md](design/CONSISTENCY_HANDOFF.md): what remains for the owner or CI before pixel-affecting work can land.

## `maintainers/`

- [MOBILE_RELEASE.md](maintainers/MOBILE_RELEASE.md): how to take a clean checkout to TestFlight and Play internal-track builds.
- [USABILITY_KIT.md](maintainers/USABILITY_KIT.md): how to run a stranger-validation session without knowing the codebase.
- [COMMUNITY_BOUNTY_DRAFTS.md](maintainers/COMMUNITY_BOUNTY_DRAFTS.md): the drafted community channel-app issues, and what is still pending approval.

## `brand/`

- [README.md](brand/README.md): the identity assets, and where each one is used.
- [gideon-mark.svg](brand/gideon-mark.svg): the transparent vector mark.
- [gideon-mark.png](brand/gideon-mark.png): the 512 x 512 application icon on a dark background.
- [gideon-mark-192.png](brand/gideon-mark-192.png): the 192 x 192 application icon for small slots.
- [avatar.png](brand/avatar.png): a 512 x 512 copy of the application icon for an avatar slot.

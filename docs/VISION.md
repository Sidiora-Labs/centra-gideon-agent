# Gideon vision

This document states what Gideon is trying to be. It is the north star. When code and
this document disagree, one of them is wrong, and the fix is deliberate either way.

For the as-built architecture, see [architecture/OVERVIEW.md](architecture/OVERVIEW.md)
and the rest of `docs/architecture/`. The plan index lives in the owner's roadmap, which
is not published in this repository.

---

## What Gideon is trying to be

Gideon is an agentic operating system for one person: a platform where agents accomplish
the user's work using a rich set of capabilities the user assembles. It is local-first
and provider-agnostic, and it is licensed under Apache 2.0, Copyright 2026 Sidiora Labs Inc.

Three experiences matter most:

1. **Goal loops.** The user gives a target; the agent classifies, plans, and loops
   autonomously, chipping away one cycle at a time, observable, pausable, resumable.
2. **Agentic chat.** Conversational sessions where the agent acts with full tool access.
3. **Automation.** Triggers, schedules and workflows that fire without the user present.

The same core ships on every delivery surface: as a local server, and through Docker
Compose. An experimental macOS-only desktop shell exists, but it is not yet built or
released by CI.

---

## Architecture tenets

1. **Provider-agnostic core.** Core owns *capability logic*; provider-specific logic
   lives in its own app bundle (native, first-party or third-party). Never hardcode a
   vendor path into core. The boundary is the app manifest plus the typed provider contract.

2. **Clean break, always.** No backward-compatibility shims, no dual implementations,
   no dead code carried forward. When a design evolves, the old path is deleted in the
   same commit. Code is the authority; stale docs are bugs.

3. **Local-first, optionally connected.** All data lives under one `~/.gideon`
   home by default. Remote backends are opt-in through provider apps. The system degrades
   gracefully to local-only and never requires the network for core operation.

4. **One path per concern.** Each piece of logic, each event transport, each storage
   access has exactly one implementation path. Dual paths are drift waiting to happen.

5. **Unattended autonomy bounded by deterministic guardrails.** The guardrails are the
   personal safety floor **for unattended work**: a daily spend ceiling, an outbound secret
   scan, provider circuit breakers, expiring trust, approval timeouts, deny-lists, single-flight
   locks, and a kill switch. They are enforced deterministically, never by the model's
   self-judgment. Interactive chat is never affected by these.

6. **As-built is the spec.** The architecture documents describe the code's current
   reality; the roadmap plans describe intended changes. No document is authoritative
   over what the code actually does.

---

## Tech design principles

- **Entities are either standalone or pluggable.** Standalone entities (sessions,
  loops, artifacts, workflows) are core-owned: their storage, lifecycle, and semantics
  are not abstractable to a third party. Pluggable entities (tasks, memory, knowledge,
  search, models, channels, tools, skills, prompts, inbox sources, triggers) have a
  typed provider contract an app can implement. Before seaming a new entity, decide
  which kind it is.

- **Apps are the extension mechanism.** The app platform runs quarantine, then the
  scan gate, then install, then a backend subprocess with a scoped token. Every piece
  of provider-specific logic ships that way. An app can host a remote backend, serve a
  UI, register MCP tools, declare crons, and contribute to any pluggable entity, and
  each of those is permission-gated.

- **The context engine is a pluggable assembly substrate.** What goes into the model
  is assembled through four hooks (ingest, assemble, compact, after_turn). New behavior
  slots in as an engine, never by hacking the assembly path.

- **One learning lifecycle.** The system improves through a single capture path (memory,
  skills and workflows), not scattered heuristics. User frustration is a signal;
  environment-dependent failures are never learned.

- **Realtime: the right transport per concern.** Always-on dashboard state rides one
  multiplexed WebSocket; page-scoped feeds get per-resource SSE. No event is
  delivered redundantly over two channels.

---

## Where it is heading

### Multi-tenant entity readiness

Gideon stays personal, and it evolves to be a **good citizen of shared stores**.
When a team hosts a shared task tracker, trigger repository, or memory space, and
a Gideon app integrates with it, the harness handles the multi-user world
gracefully:

- **Identity:** the harness knows its owner's username (first-boot onboarding,
  eventually SSO or enterprise login). Entity records carry optional contributor
  attribution defaulting to the owner.
- **Tasks:** a multi-tenant task provider may return tasks assigned to others.
  The harness displays them, filters "mine vs everyone" in views, and counts only
  the owner's items in Home widgets.
- **Triggers:** a shared trigger store may contain triggers created by others.
  The harness arms and fires only the owner's; the others are visible but inert.
- **Memory:** a shared memory provider may return memories from others. Recall
  carries contributor provenance, labeled, and ranking weights the owner's own
  contributions higher.
- **Boundary:** sharing semantics, permissions, claim and lease mechanics, and
  coordination protocols belong to the shared store or the application design,
  not to this harness.

### ACP agent backends

The native execution loop has deep harness integration. ACP-provided agent backends
(claude code, codex, kiro cli) are supported through the ACP seam. The chat runner
is provider-neutral, but in-loop machinery still has parity gaps: MCP tool delivery,
approval gates, loop guards, learning and resume. The end state is that a user who
binds one ACP provider can use the entire platform end-to-end. The measured gaps, per
provider, are in [architecture/ACP_PARITY.md](architecture/ACP_PARITY.md).

### Shared-data harnesses

Gideon's plug-and-play architecture enables one gap worth naming: **shared-data
harnesses**. A team where each member runs their own Gideon instance can plug
team-shared backends for triggers, tasks, knowledge, and memory, gaining shared
work visibility without giving up personal autonomy. The harness is never the
multi-tenant system; it is one well-behaved participant in one.

---

## How to use this document

- **Before adding a provider seam**, confirm the entity is pluggable (see above).
- **Before a refactor**, verify the relevant tenet. If unsure, the architecture
  documents have the grounded details.
- **Before deviating**, update this document first. Silence is not consent.

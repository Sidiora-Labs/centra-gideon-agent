# Chat and sessions

A session is one conversation thread for dashboard chat, a channel, a loop worker,
a webhook or a subagent. The gateway owns accepted turns and runtime acquisition;
console projections and external channel messages are consumers of that state.
Paths below are relative to the repository root.

## Session model

`runtime/gideon/engine/session.py` implements `ConversationDirectory` and the live
conversation queues. Work on one conversation is serialized, while runtime/provider
capabilities determine concurrency across conversations. `engine/session_workspace.py`
and `session_pid.py` handle working-directory and process tracking.

`runtime/gideon/engine/session_map.py` persists channel/thread links under the home.
Thread and channel identifiers are generic transport data. A stored link describes a
conversation association; it does not prove the authority of a new caller.

Session privacy modes suppress different memory operations:

- **Temporary** blocks persistent memory reads and writes.
- **Incognito** allows otherwise authorized reads and blocks writes.

The actual work principal, origin and execution lineage are bound through
`runtime/gideon/security/session_credentials.py`. An `X-Session-Key`, app label or
parent identifier supplied by a caller is not a replacement for verified work.
App-origin turns also retain current app tiers and consent. Memory and learning
consumers must use the actual reach and privacy policy.

## History and derived context

`runtime/gideon/cognition/history.py` stores conversation journals under the home.
`interfaces/dashboard/chat_persistence.py` handles dashboard persistence, metadata
and variants. The journal is authoritative; a derived compressed summary is useful
only while its covered-prefix digest still matches. Editing or rewinding a transcript
must not turn an old summary into current history.

Replay includes the authoritative epoch, turn and sequence cursor. Completed assistant
content must not be emitted again as a second streaming projection when adopting a
snapshot. The console implements this in its chat snapshot/stream helpers.

## Accepted turn pipeline

`runtime/gideon/interfaces/dashboard/chat_runner.py` coordinates the turn:

1. Resolve the actual conversation, ingress and accepted work identity.
2. Expand applicable prompt mentions and resolve agent/runtime/model bindings.
3. Assemble context through the configured engine, subject to memory reach, profile
   skill limits and app/task ceilings.
4. Stream provider events, route permission requests and retain accepted outcomes.
5. Persist the completed result and run permitted after-turn work.

Ordinary context assembly lives under `runtime/gideon/cognition`. Configured Hypermid
primary mode uses `runtime/gideon/hypermid/primary_engine.py`; it must retain the same
work reach constraints. Text-tier app work supplies task text without persistent-memory
context or tool execution. A model binding does not grant additional tools or memory.

Native and ACP runtimes have different provider contracts. ACP can forward permission,
options and compaction events only when its adapter supports them. See
[ACP comparison](../agents/acp-parity.md).

## Editing, stopping and branching

Chat actions are transactions over actual state. Stop, interrupt, edit/resend and
regenerate consumers adopt a result only when the handler accepts it and returns the
appropriate snapshot. A refused request retains the current transcript and draft.

`interfaces/dashboard/chat_regenerate.py` preserves previous answers as variants.
Fork handling creates a separate conversation and retains the original ownership/privacy
constraints; an app-scoped caller cannot use a fork to acquire an owner's session.
Persisted variants, titles, tags and folders survive reload through their native stores.

## Channels and prompts

Channel linking and handoff use the generic delivery contract, session map and
`runtime/gideon/integrations/sync_bridge.py`. Speech replies use the bound speech
provider through `integrations/voice_reply.py`. A successful dashboard event does not
itself prove channel delivery or remote handset receipt.

Prompt catalogs live under `runtime/gideon/integrations/prompt_providers`; bundled
prompt text lives under `runtime/gideon/core/config/prompts`. User prompts and snippets
are entities in the selected home. Prompt text supplies instructions, not authorization.

See [Knowledge and memory](KNOWLEDGE_MEMORY.md), [App platform](APP_PLATFORM.md),
[Security architecture](SECURITY.md) and [Architecture overview](OVERVIEW.md).

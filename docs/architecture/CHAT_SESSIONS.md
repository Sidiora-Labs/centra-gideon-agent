# Chat and sessions

How a message becomes a turn: the session model, the dashboard chat pipeline,
persistence, and the memory-privacy modes. Paths are relative to
`Gideon/src/gideon/`.

## Session model

A session is one conversation thread, whatever surface it lives on (dashboard
chat, channel thread, loop worker, webhook, subagent).

- **`session.py`: `ConversationDirectory`.** Owns live session state. Each
  session has a FIFO message queue (a `deque` of pending messages) guarded by a
  semaphore, so messages arriving on the same channel thread are serialized: a
  turn finishes before the next queued message starts.
- **`session_map.py`: the persistent session↔thread map.** Stored at
  `~/.gideon/session_map.json` with atomic tmp+rename writes. Each entry carries
  `sid`, `thread_ts` and `channel_id`, which are generic keys: no channel-vendor
  shape is assumed. `set_channel_link` / `get_channel_link` are the one API for
  linking a dashboard session to a channel thread, and a reverse index maps
  `thread_ts` to a session key.
- **`session_restrictions.py`: memory modes.** Two restriction registries,
  kept in core because any surface can request them:
  - **temporary**: a blank-slate thread. Memory reads are suppressed
    (`blocks_reads`) and writes are suppressed too.
  - **incognito**: ephemeral. Memory writes are suppressed, reads are allowed.

  `is_restricted()` (either mode) gates the after-turn learning path, session
  listing and search, and memory recall (see
  [KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)). Restricted sessions never write
  lessons: `after_turn_review.py` checks `session.is_restricted`.
- **`session_workspace.py` / `session_pid.py`**: per-session working directory
  resolution and process-id tracking.

## History and persistence

- **`history.py`**: one JSONL file per session at
  `~/.gideon/sessions/{safe_key}.jsonl`. Files rotate at 2 MB
  (`_SESSION_MAX_BYTES`), dropped lines are archived to `sessions/archive/`, and
  the archive has a 7-day retention sweep (`ARCHIVE_RETENTION_DAYS`,
  rate-limited to once per hour). Archive reads are redacted through
  `redact_credentials` / `redact_exfiltration_urls` before anything leaves the
  store.
- **`resolve_history_key()`** resolves whether a bare key is a channel-thread
  key or lives in the `dashboard:` namespace by asking the store. Core assumes
  no key shape and names no provider.
- **`dashboard/chat_persistence.py`**: the dashboard-side persistence contract
  over the JSONL store (message append, metadata, variants). Model-to-provider
  matching is data-driven via `catalog.model_family_provider_types(model)`, so
  there are no vendor names at the call site and unknown model families are
  never restricted.

## The dashboard chat pipeline

`dashboard/chat_runner.py` is the turn engine. A turn flows like this:

1. **Prompt-mention expansion**: a leading `@name key=value` expands a saved
   prompt via `_expand_prompt_mention` (user prompts live at
   `~/.gideon/prompts/`, snippets at `prompt_snippets/`; the composer's @-menu
   suggests prompts only at message start).
2. **Context assembly**: `context.py` (`PromptAssembler`) builds the system
   context: the `{{bot_name}}` variable (live-resolved from `agent.bot_name`),
   memory context, and, for channel-linked sessions, the
   `channel-thread-context` snippet. `context_engine.py` and
   `context_compaction.py` manage sizing and compaction.
3. **Agent resolution**: the selected agent's prompt governs. Task-mode
   posture is layered as a `system_prompt_suffix` on top of the resolved agent
   prompt, never as a replacement (see `chat_runner.py` around the
   `system_prompt_suffix` call site).
4. **Model resolution**: the `chat` use-case binding from
   `active_models.json`, unless the agent pins a model or the composer
   overrides per-session. The `model` kwarg threads through `llm/registry.py`
   `registry.build`, and every factory honors it.
5. **Streaming and persistence**: chunks stream over the dashboard WebSocket,
   and the finished turn appends to the session JSONL.

Around the engine:

- **`dashboard/chat_handlers.py`**: session listing and history. Channel-linked
  rows carry `origin="channel"`, computed at list time from the session map and
  never persisted; the frontend `ChatPage.tsx` switches tabs on that literal.
- **`dashboard/chat_title.py`**: auto-title plus optional auto-tagging in one
  background LLM call (config `dashboard.auto_tag_sessions`); `chat_retag.py`
  is the batch re-tag job (cancellable, board-triggered).
- **`dashboard/chat_folders.py` / `chat_tags.py`**: organization, persisted in
  `folders.json` / `tags.json`.
- **`dashboard/chat_channel.py`**: channel link and handoff routes
  (`POST /api/chat/sessions/{session}/channel-link`,
  `GET /api/channels/reply-targets`). They are provider-blind and built on
  `ChannelDelivery` only (see [INBOX_CHANNELS.md](INBOX_CHANNELS.md)).
- **`dashboard/chat_voice.py`**: `POST /api/voice/synthesize`, sentence-chunked
  TTS through `tts.registry.active_voice_params` (whatever TTS provider is
  bound).

## Variant branching (regenerate)

`dashboard/chat_regenerate.py`: regenerating an assistant message preserves the
prior answer as a **variant**. The message's `variants[]` list (capped at
`_MAX_VARIANTS`) plus `variant_idx` are persisted in the session JSONL, so the
user can flip between alternative answers and the choice survives a reload. The
backend broadcasts variant switches, and the frontend renders prev/next
navigation on the message.

## Forking

`dashboard/chat_fork.py`: `POST /api/chat/sessions/{session}/fork` copies a
session into a new tab. App-scoped callers may only fork sessions they own: the
`app` claim is checked, and unscoped sessions are denied to apps.

## Channel-linked sessions

A dashboard session can be linked to a channel thread, and the other way round:

- Linking goes through core `session_map.set_channel_link`; the channel app
  never touches the map file directly.
- `sync_bridge.py` implements the dashboard↔channel handoff
  (`handoff_to_channel` over `ChannelDelivery`), so the conversation continues in
  the channel with context intact.
- `voice_reply.py` uploads TTS voice replies to the channel
  (`upload_voice_to_channel`); markdown deep links are stripped generically
  before synthesis.

## Prompt entities

`prompt_providers/` is the prompt-catalog subsystem (bundled use-case prompts
plus user prompts). Use-case prompts include, for example, `task-channel-title`
(use case `channel_title`) for naming channel-originated tasks. All bundled
prompts are provider-blind.

## Related docs

- Memory recall and write rules per session mode:
  [KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)
- Channel delivery and thread linking: [INBOX_CHANNELS.md](INBOX_CHANNELS.md)
- The agent and tool layer a turn can reach:
  [OVERVIEW.md](OVERVIEW.md#capability-seams)

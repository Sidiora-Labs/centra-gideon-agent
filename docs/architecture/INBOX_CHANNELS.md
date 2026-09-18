# Inbox and channels

Two seams connect Gideon to the outside conversational world: the **inbox**
(things arriving for the user's attention) and **channels** (bidirectional
messaging surfaces like Slack, implemented entirely by apps against core
protocols). Paths are relative to `Gideon/src/gideon/`.

## Inbox

- **`inbox.py`**: the item store. **`inbox_service.py`**: the service loop. It
  polls sources every 60 seconds, ingests with dedup and mute/dismiss filters
  (muted threads are dropped at ingestion), and evaluates **alerts at ingestion
  time** for both the push and the poll path (`evaluate_alert` →
  `notify_inbox_alert`), so an alerting item notifies immediately instead of on
  the next page view. Maintenance (retention cleanup + dismissed-set pruning) is
  not on that loop: it is the `inbox.maintenance` remediation job, measured from
  the live service's backlog and run by the remediation engine.
- **AI drafts** write on behalf of the operator (the `dashboard.user_name`
  identity), not the bot.
- **Sources**: `inbox_providers/` ships native push and filesystem sources; the
  seam is entry-point discoverable (`provider_registry.py`), and apps contribute
  their own. A **channel app is expected to register one**: the
  vendor-completeness pattern makes the channel transport and the inbox message
  source two seams of one bundle, so messages arriving while no session is live
  still reach the Inbox. See
  [BUILD_A_CHANNEL_APP.md](../guides/BUILD_A_CHANNEL_APP.md) for the checklist.
- **Settings** live solely in
  `~/.gideon/entity_settings/inbox.json` (`auto_cleanup_enabled`,
  `retention_days`) with type- and range-guarded PUTs in
  `providers/entity_routes.py`. **Alerting is no longer here:** the former
  `alert_keywords` and `alert_on_name_mention` fields were retired and
  generalized into per-kind rule `conditions` (below), so the same keyword or
  name-mention escalation now applies to loop requests and proposals, not just
  to messages. `inbox.evaluate_alert()` reads the `inbox/alert` rule's
  conditions.

## Notifications

`ConsoleState.notify()` (`dashboard/state.py`) is the **single choke point** for
user-facing notifications. Two layers of policy apply, in this order.

**1. The global gate**: `notification_allowed()`
(`providers/entity_routes.py`), unchanged and outermost:

- severity rank map with `min_severity`;
- midnight-wrapping quiet hours (severity-3 bypasses them);
- `mute_all`;
- suppressed means **dropped entirely**, not queued, and a gate failure fails
  open, because a broken settings file must not silence the system.

Preferences persist in `entity_settings/notifications.json` with enum and HH:MM
domain-guarded PUTs.

**2. The per-kind rule**: `notification_rules.py`. Every notification is a
registered `(source, kind)` pair (`notification_kinds.py`), and each pair
resolves to a rule:

- **mode**: `never` (drop), `badge` (persist without a toast), `immediate`
  (deliver), `digest` (batch into `digest_queue.jsonl` for the scheduled
  summary);
- **targets**: `dashboard` today; `channel_dm` via
  `ChannelDelivery.deliver_notification`; `push` and `native` are accepted and
  persisted but inert until the apps/mobile/desktop plans land;
- **conditions**: keywords or name-mention that **escalate** a quieter mode to
  `immediate`. Escalation is capped at `immediate` and never adds targets the
  user did not choose.

Rules live in `entity_settings/notification_rules.json` with a guarded
`PUT /api/notifications/rules`, and the matrix is Settings → Notifications →
Per-kind delivery. **Rules refine delivery for notifications that already passed
the gate; they can never resurrect a suppressed one**, so `mute_all` still means
mute. Every failure path (missing file, malformed JSON, unknown mode or target)
falls back to the registry default, which is `immediate`: a policy layer that
cannot read its own config must not be able to silence the system. An
unregistered pair resolves to `system/generic` with a warning rather than
raising.

Unread counts are *derived* from unacked log entries, and deletes broadcast
`notification_removed`. Notification metadata may carry a `channel_link`, built
via `ChannelDelivery.build_thread_link`, never by core string-formatting a vendor
URL.

## Channels: the two core seams

Core owns two protocols and **zero vendor code**.

### Inbound: `channel_transports/`

`base.py` defines `ChannelTransportProvider`, and `manager.py` is the registry.
The gateway iterates `list_transports()` and calls each transport's
`start_inbound(services)` with the `GatewayServices` object
(`gateway_services.py`: sessions, context builder, conversation log,
consolidator, cron service, subagent manager, channel history, dashboard state,
config, owner id). Two implementations ship in-tree: `webui.py` (the dashboard
itself as a transport) and `reference_echo.py` (a minimal example).

### Outbound: `channel_delivery.py`

The `ChannelDelivery` protocol: `open_dm`, `deliver_text`, `deliver_rich`,
`deliver_cron_result`, `deliver_notification`, `deliver_chat_mirror`,
`deliver_subagent_reply`, `resolve_user_name`, `resolve_user_profile`,
`channel_info`, `list_reply_channels`, `is_tracked_channel`,
`build_thread_link`, `upload_attachment`, and the streaming primitives
(`start_stream` / `append_stream_task` / `stop_stream`), plus
`request_approval`. Everything the gateway sends outward flows through the
registered implementation.

### Vendor-blind grammar

The delivery vocabulary names no vendor anywhere in core:

- background results route via `deliver="channel[:<chan>:<ts>]"`;
- the `notify` MCP tool's session enum is `["origin", "channel"]`
  (`mcp_core.py`);
- `/api/send-message` responds `{"ok", "channel", "session"}`;
- Security Event Log labels use `downstream_service="channel"`;
- chat-history rows carry `origin="channel"` (see
  [CHAT_SESSIONS.md](CHAT_SESSIONS.md)).

## The reference channel app: `apps/slack-channel`

`apps/slack-channel/slack_runtime/` is the full worked example of a channel
provider. The modules that carry the contract:

- `transport.py`: implements `start_inbound`;
- `runtime.py`: a facade proxying `GatewayServices`;
- `delivery.py`: `SlackDelivery`, the `ChannelDelivery` implementation,
  including the vendor deep link behind `build_thread_link`;
- `events.py` / `handler.py` / `interactions.py`: inbound event routing;
- `blocks.py` / `format.py` / `files.py`: vendor message formats;
- `allowlist.py` / `enterprise.py`: access control;
- `settings.py`: the app-owned `SlackSettings` store with a loud, retry-safe
  `migrate_from_core()`. All channel config lives app-side; core's config loader
  defines no channel dataclasses.

Why it is shaped this way, and which small Slack-named constants deliberately
remain in core, is covered in [PROVIDER_BOUNDARY.md](PROVIDER_BOUNDARY.md).

## Channel-thread and session linking

- The persistent map is core: `session_map.py` `set_channel_link` /
  `get_channel_link`, with generic `thread_ts` / `channel_id` keys. Channel apps
  go through these calls and never touch the map file.
- Dashboard-side link and handoff routes are `dashboard/chat_channel.py`
  (`POST /api/chat/sessions/{session}/channel-link`,
  `GET /api/channels/reply-targets`), provider-blind and `ChannelDelivery` only.
- `sync_bridge.py` hands a dashboard conversation off to a channel thread
  (`handoff_to_channel`), and `voice_reply.py` uploads TTS voice replies
  (`upload_voice_to_channel`).
- `channel_history.py` keeps a rolling per-channel message window
  (`observe_max_messages` / `observe_ttl_hours`, generic top-level config keys).

## Related docs

- Building a new channel (the must/should/may obligation tables, trust and
  pairing, the conformance kit, vendor completeness):
  [BUILD_A_CHANNEL_APP.md](../guides/BUILD_A_CHANNEL_APP.md)
- Session model and memory modes on channel threads:
  [CHAT_SESSIONS.md](CHAT_SESSIONS.md)
- The boundary judgments behind the channel split:
  [PROVIDER_BOUNDARY.md](PROVIDER_BOUNDARY.md)
- How a channel app is installed and sandboxed:
  [APP_PLATFORM.md](APP_PLATFORM.md)

# Inbox, notifications, and channels

The Inbox collects items requiring attention. Notifications decide how attention is
presented. Channels connect conversations and delivery to external messaging services.
These are separate contracts; a channel app can contribute an inbox source or automation
event source when its vendor supports those capabilities.

## Inbox

`runtime/gideon/integrations/inbox.py` owns the item store, deduplication, filters, and
alert evaluation. `runtime/gideon/integrations/inbox_service.py` runs source polling and
maintenance. Polling uses the configured inbox interval, with a 60-second fallback;
retention maintenance runs on a six-hour interval. Push and polling ingestion both
participate in the alert path.

Source contracts and the registry live under
`runtime/gideon/integrations/inbox_providers/`. Native push and filesystem sources are
available; installed apps can contribute other sources. Draft generation and dispatch
remain separate from receiving an item: an available draft is not evidence that a
message was sent.

Inbox entity settings live under the active `GIDEON_HOME` in
`entity_settings/inbox.json`. Per-kind notification rule conditions provide keyword and
name-mention escalation. API validation and current source availability determine
which operations are admitted; an inbox item does not itself authorize agent work.

## Notification policy and delivery

`ConsoleState.notify()` in `runtime/gideon/interfaces/dashboard/state.py` composes and
routes notifications. Platform-owned metadata is protected from supplied metadata.
Notifications for another addressee follow the registered notification-provider route
rather than being broadcast as local owner messages.

For locally addressed notifications, global posture applies before the per-kind rule:

- Mute-all and minimum severity are hard limits.
- Quiet hours constrain interruptions while preserving applicable badge and digest
  behavior; they do not simply delete every notification.
- Per-kind modes are `never`, `badge`, `immediate`, and `digest`. Keyword or name-mention
  conditions can escalate attention within the global posture.

The implementation is in `runtime/gideon/workspace/notification_rules.py` and
`runtime/gideon/workspace/notification_kinds.py`, with settings validation in
`runtime/gideon/extensions/providers/entity_routes.py`. Rules persist in
`entity_settings/notification_rules.json`; digest items use a bounded queue. Missing or
malformed policy falls back to defaults rather than silently suppressing attention.

Targets include `dashboard`, `channel_dm`, `push`, and `native`. Channel delivery requires
an available transport and an authorized destination. Push dispatch uses content-free
payloads and configured subscriptions. Native delivery depends on the connected desktop
shell's reported capability. These are implemented routes, but a configured target does
not prove a phone or operating system displayed the notification.

Unread counts derive from persisted unacknowledged entries. Dashboard notification
removal is broadcast. A vendor conversation link comes from the app's delivery contract,
not a vendor URL invented by core.

## Channel contracts

`runtime/gideon/integrations/channel_transports/base.py` defines the transport contract;
its manager tracks registered transports. The gateway provides `GatewayServices` when
starting an inbound receiver. Each app owns its vendor connection and normalizes messages
before entering shared trust and session routing.

`runtime/gideon/integrations/channel_delivery.py` defines the rendering protocol:
text and rich replies, attachments, streaming, scheduled results, notifications, links,
profile information, and approval prompts. Apps import these contracts through
`gideon.sdk.channel`. The transport's outbound result can include an explicit structural
refusal; failed or refused delivery must remain distinguishable from transmission.

The bundled Slack reference is at
`runtime/gideon/extensions/apps/native/gideonai-slack-desk/slack_desk_runtime/`.
Its transport, runtime adapter, delivery, event handlers, approval helpers, client, and
settings are app-owned. The reference is an implementation to inspect, not a claim that
any particular installation has Slack credentials or a working connection.

## Trust, approvals, and session links

Every inbound path must call the shared trust guard before session dispatch or event
publication. Default DM access requires pairing; group access depends on tracking.
Protected owner identity is distinct from an allowed conversational sender. Preserve the
shared fence on non-owner content.

Approval replies must match the actual pending event and its exact offered scope.
One-call prompts do not authorize standing grants. Reconcile dashboard and channel
answers through the shared controller rather than maintaining independent decisions.

`runtime/gideon/engine/session_map.py` stores channel/session associations. Generic
channel-link and reply-target routes live in
`runtime/gideon/interfaces/dashboard/chat_channel.py`; synchronization and voice reply
adapters live in `runtime/gideon/integrations/sync_bridge.py` and
`runtime/gideon/integrations/voice_reply.py`. Channel history is maintained separately
from the authoritative conversation journal.

See [building a channel app](../guides/BUILD_A_CHANNEL_APP.md) for SDK usage,
[chat sessions](CHAT_SESSIONS.md) for history and privacy, and
[provider boundaries](PROVIDER_BOUNDARY.md) for contribution ownership.

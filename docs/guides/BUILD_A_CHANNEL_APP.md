# Build a channel app

A channel app connects Gideon to an external messaging service. It owns the vendor
client, inbound normalization, and outbound rendering. Register those contributions
through the app manifest and import gateway contracts through `gideon.sdk.*`.

Use the bundled Slack implementation under
`runtime/gideon/extensions/apps/native/gideonai-slack-desk/` as a concrete reference.
Other channel bundles depend on the catalogue configured by your operator; this guide
does not assume an external apps repository is available.

See [the app platform](../architecture/APP_PLATFORM.md),
[inbox and channels](../architecture/INBOX_CHANNELS.md), and
[provider boundaries](../architecture/PROVIDER_BOUNDARY.md).

## Transport and delivery

Import `ChannelTransportProvider`, `ChannelCapabilities`, `ChannelMessage`,
`OutboundMessage`, and `ChannelDelivery` from `gideon.sdk.channel`.

| Contract | Responsibilities |
|---|---|
| `ChannelTransportProvider` | Stable provider name and display name, connection lifecycle, outbound `send`, declared capabilities, health, and test information. |
| `ChannelDelivery` | Reply rendering, files, streaming, approval prompts, conversation links, and supported notification destinations. |

The transport's `send(OutboundMessage)` contract accepts `bool`, a string result, or a
structural refusal carrying `channel`, `target`, `reason`, and boolean behavior. Preserve
an explicit refusal; do not turn “not transmitted” into a successful delivery report.

Override the lifecycle methods your transport needs. An inbound provider uses
`start_inbound(services)` and `stop_inbound()` to own and stop its receiver. Normalize
vendor messages into the channel message shape and route them using the supplied
`GatewayServices`; do not start a second competing chat runtime.

Use a stable `name`: trust, settings, and tracking state refer to that identity. Keep
`info()`, `health()`, and `test()` consistent with the real connection. A passive health
check and an active vendor probe may observe different conditions, so report those
conditions explicitly rather than claiming a healthy connection without evidence.

## Declare capabilities honestly

`ChannelCapabilities` includes `inbound`, `threads`, `attachments`, `reactions`, `edits`,
`rich_text`, `typing_indicator`, `max_text_len`, `groups`, `owner_pairing`, and
`speaks_as_owner`. Most flags default to `False`; `max_text_len=0` means no declared
limit. Enable only capabilities the implementation actually supports.

If edits are supported, implement the streaming lifecycle and flush final output when
stopping a stream. Apply vendor rate limits and surface failed sends. If a capability
is unavailable, document it and keep its declaration false.

## Gate every inbound path

Call the shared trust guard before dispatching a message to a session or publishing it
as an automation event:

```python
from gideon.sdk.channel import guard_inbound

verdict = guard_inbound(
    state, self.name, sender_id,
    sender_name=sender_name,
    channel_id=channel_id,
    is_dm=is_dm,
    text=raw_text,
)
if not verdict.allowed:
    if verdict.canned_reply:
        await self.delivery.deliver_text(channel_id, verdict.canned_reply)
    return
text_for_session = verdict.fenced_text or raw_text
```

Keep sender and conversation identities derived from authenticated vendor payloads.
The default DM policy requires pairing; default group access requires tracking. Shared
helpers own pairing, allow/deny state, tracked conversations, and fencing. Do not create
a second vendor-local trust store. Non-owner content must retain the shared fence when
it reaches the model.

Owner identity comes from the protected credential store, not a user-editable settings
field or a message's claimed display name. An open DM setting does not establish that
its sender is the owner. Distinguish permission to converse from permission to answer
owner approvals.

## Preserve offered approval scope

`ChannelDelivery.request_approval()` returns `True`, `False`, or `None` when the channel
cannot prompt and dashboard fallback is needed. Use the core-composed brief and its
exact offered answers. The SDK exposes `offered_answers` and `ONE_CALL_ANSWERS`; do not
invent an “always allow” action when the actual prompt offers only one-call approval.

Bind the answer to the actual pending event, protected owner identity, and live approval
state. A reply in another thread, a stale action, or an untrusted sender must not grant
permission. Coordinate with the dashboard using the supplied `on_prompted` callback and
session handle instead of maintaining competing decisions or timeouts.

## Register the bundle

A minimal registration has this shape; add real permissions and settings as required:

```json
{
  "name": "your-channel",
  "version": "0.1.0",
  "displayName": "Your Channel",
  "description": "Connect Gideon to Your Channel.",
  "provider": {
    "type": "channel",
    "implementation": "your_runtime.transport:create_provider"
  }
}
```

Versions use the manifest's bounded PEP 440 validation. Additional contributions go in
`providers`; for example, an inbox source uses `type: "inbox"`, and an automation event
source uses `type: "trigger_source"`. Use `gideon.sdk.trigger_source` for
`TriggerSourceProvider` and `SourceEvent`.

Declare applicable companion contributions rather than assuming every vendor provides
the same features. A shared app-local inbound stream can feed transport and trigger
adapters without opening duplicate vendor connections. Publish only admitted events,
choose event names from declared structural categories, and place untrusted prose in
`SourceEvent.text` so core can fence it. Do not use user prose to select event names or
smuggle it into matched metadata.

Store secrets through the credential SDK, not in the manifest or committed settings.
Review the install preview's permissions, programs, and dependencies. Add a bundle-owned
UI page only when the generic provider settings cannot express the interaction.

## Check the contract and the real integration

`assert_channel_contract` and `assert_channel_approvals` are exported through
`gideon.sdk.channel`. The conformance helper checks identity, capabilities, lifecycle,
trust/fencing, supported delivery methods, and streaming when supplied the relevant
clock and edit observations. Run checks with an isolated `GIDEON_HOME` so trust fixtures
cannot modify your real store.

The current conformance helper still requires a boolean `send` result, while the
transport interface also permits strings and structural refusals. Account for that
coverage gap when testing those results; a helper failure is not a reason to discard a
real refusal. Missing companion-provider warnings are advisory, not an install grant.

Contract checks do not prove vendor authentication, rate-limit recovery, actual message
delivery, or owner approval identity in a live deployment. Test those journeys against
the real integration, including unknown senders, untracked groups, rejected approvals,
disconnection, and final stream delivery. Report precisely which paths were exercised.

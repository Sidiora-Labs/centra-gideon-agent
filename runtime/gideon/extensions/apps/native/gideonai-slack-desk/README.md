# GideonAI Slack Desk

Slack desk for Gideon agents. Monitor channels, answer mentions, and act on threads and DMs.

**GideonAI Slack Desk** is a **channel-transport provider** — it implements the
`gideon.sdk.channel` `ChannelTransportProvider` contract and shows up under the
messaging channels.

## What this is

A standalone Gideon app bundle (part of the core/app workspace split). It ships
as a self-contained directory:

- `app.json` — the manifest (provider type + `implementation`; Tier-2 apps carry no `native` flag — that's Tier-1-only).
- `slack_desk_runtime/` — the implementation, exposed via `transport:create_provider`.
- `test_provider.py` — the app's own tests.

It imports only the Gideon **SDK** (never core internals), so core can evolve
without breaking it:

- `gideon.sdk.channel`
- `slack-sdk` (the upstream Slack client library, declared in `app.json`)

## Automations from inbound traffic

This bundle also registers a **`trigger_source`** provider
(`slack_desk_runtime.trigger_source:create_provider`), so a message that arrives here can fire a
`kind: event` automation. Event names are namespaced by core from the app name:

- `app:gideonai-slack-desk:direct_message` — a message in a DM with the bot.
- `app:gideonai-slack-desk:channel_message` — a message in a tracked channel, or an @mention in one.

Author a trigger with pattern `AppEvent` and an `event_glob` matching one of those (or
`app:gideonai-slack-desk:*` for any of them). The message body arrives as the payload, **fenced at
origin** by core; `meta` carries identifiers only (`channel_id`, `sender`, `thread_id`,
`is_dm`).

Three things this deliberately does *not* do:

- **It observes nothing your trust gate refused.** The publish happens at the one point
  where a message has cleared this app's allowlist / open-channel / tracked-channel gate,
  its channel activation mode and its dedup cache. (This bundle predates core's guarded door —
  see `tests/test_conformance.py`'s strict xfail for T1.4 — so the gate here is its own.)
  A denied sender gets no session and arms no automation.
- **The event name never comes from the message.** It is chosen in code from the frozen
  list above by a structural fact, so a sender cannot pick which of your automations runs.
- **Prose never lands in `meta`.** `meta` is matched, not narrated, and core does not fence
  it — so a sender's Slack profile name is not there.

## Install

From the App Store, add the `apps/` directory as a **local source**, then install
**GideonAI Slack Desk** — the install runs through the security scanner and lifecycle exactly like
any other app. (Or `POST /api/apps {"source": ".../apps/gideonai-slack-desk"}`.)

## Settings

| Key | Label | Notes |
|---|---|---|
| `bot_token` | Bot Token | Slack Bot User OAuth Token (xoxb-...). Outbound only needs this one. |
| `app_token` | App Token | Slack App-Level Token for Socket Mode (xapp-...). **Inbound needs both.** |
| `allowed_users` | Allowed Users | Who may talk to the bot, besides the owner: `{slack_id, name}` per entry. Empty means owner-only; with no owner set either, nobody is authorized. |

Both tokens are **write-only**: once saved, the form shows `••••••••` and the value never
leaves the gateway. Saving other fields keeps the stored tokens; typing a new value
replaces one.

**Inbound starts at gateway boot**, so tokens saved into a running gateway apply on the
next restart. Until then the channel row reports the inbound half honestly — "Outbound
ready, inbound NOT STARTED" — rather than a flat green.

An allowlisted (non-owner) user is authorized for conversation *and* for the commands in
the "any allowed user" tier — `!stop`, `!title`, `!compact`, `sessions`, and `!dashboard`,
which DMs them a dashboard session link. Everything else stays owner-only. Add people
deliberately.

### Settings that currently do nothing

Two keys are visible in the Configure form and have no effect. They are listed here rather
than quietly left in place:

- `open_channels` — "all users authorized in this channel" is not enforced; the predicate
  behind it is a hardcoded `false`.
- `allowed_enterprise_ids` — workspace validation accepts any workspace whose bot token
  authenticates; the list does not restrict it.

Making either live changes *who can reach the agent*, so it is a deliberate decision rather
than a bugfix. Until then, `allowed_users` (above) is the allowlist that is enforced.

## The live-writes kill switch

A `chat.postMessage` is a live, outward write a whole workspace sees — and is
notified about — before any undo could run, so `send()` honors the platform's
process-wide `GIDEON_DISABLE_LIVE_WRITES` switch, the same one core applies to
non-GET egress and local-model deletion.

With the switch set, `send()` transmits nothing and returns a **typed refusal**
(`SendRefused`) instead. It is falsy, so every existing "did it send?" caller keeps
reading "not delivered" unchanged, but a caller that cares can tell a suppressed write
from a failed one with `isinstance(result, SendRefused)` — the two demand opposite
responses, and a bare `False` would conflate them.

Parsing follows the platform's fail-safe rule exactly: an **absent** variable allows
writes (the switch is opt-in), an explicit `0`/`false`/`no`/`off` turns the guard off,
and **any other present value — including a typo — turns it on**.

## Slack app setup

1. Go to <https://api.slack.com/apps> → **Create New App** → **From a manifest**,
   and paste `slack-desk-manifest.yaml` (replace `{{USERNAME}}` with your name).
2. Socket Mode: toggle it OFF then back ON to trigger the token-generation
   dialog → add the `connections:write` scope → **Generate** → copy the
   `xapp-...` App-Level Token.
3. **Install to Workspace** and copy the Bot User OAuth Token (`xoxb-...`).
4. Enter both tokens in the app's Configure form (Settings above), or run
   `gideon setup` and paste them when prompted.

The first person to DM the bot is auto-claimed as the owner. Use
`/gideon @user` to allowlist more users and `/gideon #channel` to
track a channel.

## License

MIT — see `LICENSE`.

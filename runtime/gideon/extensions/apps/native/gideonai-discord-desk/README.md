# Discord Desk

Discord Desk pairs a Discord bot with Gideon for DM and server conversations.
Converse in tracked channels, receive results with approval buttons, and fire
automations from inbound messages.

**Discord Desk** is a **channel-transport provider** — it implements the
`gideon.sdk.channel` `ChannelTransportProvider` contract and shows up under the
messaging channels alongside the dashboard, Slack and Telegram.

> **Read this first: enable the MESSAGE CONTENT intent.** Discord treats it as a
> *privileged* intent and ships it off by default; without it every message arrives
> with an **empty `content`**. The bot then connects, shows as online, receives
> events — and ignores everything you say. This is the single most common reason a
> Discord bot looks broken. Developer Portal → your application → **Bot** →
> **Privileged Gateway Intents** → enable **Message Content Intent**.

## What this is

A self-contained Gideon app bundle. It ships as one directory:

- `app.json` — the manifest (provider type `channel`; `implementation` points at
  `discord_desk.transport:create_provider`).
- `discord_desk/` — the implementation:
  - `gateway.py` — the Gateway WebSocket client over `websockets` (no vendor SDK):
    identify with the intents bitfield, heartbeat + ACK tracking, session resume,
    and dispatch of `MESSAGE_CREATE` / `INTERACTION_CREATE`. Carries the
    **zombie-connection** detection (a heartbeat still unacked when the next one is
    due means the gateway stopped processing us even though the socket looks open).
  - `api.py` — a thin `httpx`-backed REST client (no vendor SDK), with the one piece
    of real logic Discord forces on every caller: **per-bucket** rate limiting, and a
    global 429 tracked separately from a per-route one.
  - `transport.py` — the inbound event path (trust seam, self-message filter) plus
    the outbound `send`.
  - `delivery.py` — the `ChannelDelivery` the gateway delivers results through
    (message splitting, throttled edit-streaming, button approvals, reactions).
  - `settings.py` — this app's own DM-activation / application-id config and the
    credential key.
- `discord_desk_setup.py` / `discord_desk_doctor.py` — the `gideon setup` /
  `gideon doctor` hooks.
- `test_discord_desk.py` + `tests/` — the bundle's own test suite.

Core is imported **only** through the Gideon **SDK** (never core internals), so
core can evolve without breaking this bundle:

- `gideon.sdk.channel` — transport ABC, `ChannelMessage`, the sender-trust seam
  (`guard_inbound`), redaction, `run_chat`, `ProviderSettings`.
- `gideon.sdk.cli` — `SetupContext` / `DoctorLine`.

Both wire protocols are implemented directly against libraries that are **already
core dependencies** (`httpx`, `websockets`), so this bundle declares no
`pythonDependencies` at all — there is no `discord.py` or any other vendor SDK
anywhere in the directory, tests included.

Who may talk (allowlist, pairing) and which server channels are tracked are owned
by the **core sender-trust seam** (`channel_trust`, provider `"discord"`) — this
bundle keeps no allowlist of its own. The bot token is a secret in the shared
credential store under this bundle's own `DISCORD_BOT_TOKEN` key. The
**application id** is *not* a secret (Discord prints it publicly and it appears in
every invite URL), so it lives in this bundle's own settings where you can see and
edit it.

## Automations from inbound traffic

The directory also registers a **`trigger_source`** provider
(`discord_desk.trigger_source:create_provider`), so an arriving message can fire a
`kind: event` automation. Core namespaces the event names from the app name:

- `app:gideonai-discord-desk:direct_message` — a message in a DM with the bot.
- `app:gideonai-discord-desk:guild_message` — a message in a tracked guild channel
  or thread.

Author a trigger with pattern `AppEvent` and an `event_glob` matching one of those
(or `app:gideonai-discord-desk:*` for any of them). The message body arrives as the
payload, **fenced at origin** by core; `meta` carries identifiers only (`channel_id`,
`sender`, `guild_id`, `is_dm`).

Three things this deliberately does *not* do:

- **It observes nothing your trust gate refused.** The publish happens after core's
  guarded door returns `allowed`. A denied sender gets no session and arms no
  automation.
- **The event name never comes from the message.** It is chosen in code from the
  frozen list above by a structural fact, so a sender cannot pick which of your
  automations runs.
- **Prose never lands in `meta`.** `meta` is matched, not narrated, and core does
  not fence it — so an author's chosen `global_name` is not there.

## Install

From the App Store, add the `apps/` directory as a **local source**, then install
**Discord Desk** — the install runs through the security scanner and lifecycle
exactly like any other app.
(Or `POST /api/apps {"source": ".../apps/gideonai-discord-desk"}`.)

## Settings

| Key | Label | Notes |
|---|---|---|
| `bot_token` | Bot Token | Discord bot token from the Developer Portal. Sent as `Authorization: Bot <token>` — the literal `Bot ` prefix is mandatory. Stored as a secret. |
| `application_id` | Application ID | The application's public ID. Used to build the bot invite URL. Not a secret. |
| `dm_activation` | DM Activation | `always` (answer every paired DM), `mention` (only when @-mentioned), or `off`. A DM posture only — it never gags a tracked server channel. |

## The live-writes kill switch

Posting a Discord message is a live, outward write everyone in the channel sees
immediately, so this transport honors the platform's process-wide
`GIDEON_DISABLE_LIVE_WRITES` switch — the same one core applies to non-GET egress
and local-model deletion.

With the switch set, `send()` transmits nothing and returns a **typed refusal**
(`SendRefused`) instead. It is falsy, so every existing "did it send?" caller keeps
reading "not delivered" unchanged, but a caller that cares can tell a suppressed
write from a failed one with `isinstance(result, SendRefused)` — the two demand
opposite responses, and a bare `False` would conflate them.

Parsing follows the platform's fail-safe rule exactly: an **absent** variable allows
writes (the switch is opt-in), an explicit `0`/`false`/`no`/`off` turns the guard
off, and **any other present value — including a typo — turns it on**.

## Discord bot setup

1. Go to <https://discord.com/developers/applications> → **New Application**, name
   it.
2. **General Information** → copy the **Application ID**.
3. **Bot** → **Reset Token** → copy the token (shown once).
4. **Bot** → **Privileged Gateway Intents** → enable **MESSAGE CONTENT INTENT**.
   (See the warning at the top — skip this and the bot receives empty messages.)
5. Run `gideon setup` and paste the token, application id and your own Discord user
   id (enable Settings → Advanced → **Developer Mode**, then right-click your name →
   **Copy User ID**). The setup step then prints the **OAuth2 invite URL** with the
   permission bits already computed — open it and pick a server.
6. Track the channels you want the bot active in from the Channels page.

The invite requests exactly the permissions the code exercises: View Channels, Send
Messages, Send Messages in Threads, Add Reactions, Attach Files, Read Message
History. Nothing broader.

## How trust works

The transport enforces nothing itself — every inbound message goes through the core
seam, so the policy is identical across channels:

- **DMs pair.** An unknown DM sender gets a canned pairing-needed reply and is never
  routed; run `gideon pair discord` for a code. The owner also gets one actionable
  notification per unknown sender (deduped, restart-durable).
- **Server channels are tracked-only.** A message in an untracked channel is dropped
  silently — no owner spam.
- **Non-owner server content is fenced.** It enters the session wrapped so the model
  reads it as data, not instructions.
- **The bot ignores itself.** `MESSAGE_CREATE` fires for the bot's own sends, so
  self-authored (and other-bot) messages are dropped. Without that filter a bot
  answers its own replies forever.

## Tests

```
python -m pytest gideonai-discord-desk -q
```

No network, no wall-clock sleeps, no vendor SDK: the REST client runs against an
`httpx.MockTransport`, the gateway against a scripted fake WebSocket, and the
heartbeat/throttle clocks are injected. Covers the gateway lifecycle
(identify/heartbeat/ack/resume/dispatch, zombie detection, invalid-session), the
per-bucket and global 429 paths, the approval button round-trip, and the trust
integration against the **real** core seam in an isolated home.

### Owner validation step (not automated)

Validating against a **real Discord application, bot and test server** is an
**owner real-world step** and is deliberately outside automated execution — it
needs a human to create an application in the Developer Portal, enable the
privileged intent, and invite the bot to a server. The automated suite above covers
the protocol; it cannot cover "Discord accepted this token". Run the Channels page
→ Discord → **Test** action for the live gateway-hello probe once configured.

## License

MIT — see `LICENSE`.

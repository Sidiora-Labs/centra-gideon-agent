# Mail Desk

GideonAI's two-way mail desk over stdlib IMAP and SMTP. Write the bound mailbox and the
agent answers in the same thread; cron results, notifications and approvals land there
too.

**Mail Desk** is a **channel-transport provider** — it implements the
`gideon.sdk.channel` `ChannelTransportProvider` + `ChannelDelivery` contracts and shows up
among the messaging channels alongside the dashboard, Slack, Telegram and Discord.

## Not the same as Mail Inbox

The sibling **`mail-inbox`** app is an *inbox source*: it surfaces mail as read-only inbox
items behind its own sender allowlist. This app is a *channel*: you converse with the
agent through mail, and it replies into the thread. Two different seams, two different
trust owners:

| | `mail-inbox` | `gideonai-mail-desk` (this app) |
|---|---|---|
| Seam | `MessageSourceProvider` (inbox) | `ChannelTransportProvider` + `ChannelDelivery` |
| Direction | inbound only | two-way |
| Who may talk | app-local `allow_senders` globs | the **core trust seam** (`channel_trust`, provider `mail-desk`) |
| Result | an inbox item | a conversational turn, answered in-thread |

Install both if you want mail to *trigger* things (mail-inbox) **and** to *talk* to your
agent (this app). They poll independently and hold separate cursors.

## What this is

A standalone Gideon app bundle. It ships as a self-contained directory:

- `app.json` — the manifest (provider type `channel`; `implementation` points at
  `mail_desk_runtime.transport:create_provider`).
- `mail_desk_runtime/` — the implementation:
  - `imap_client.py` — the blocking IMAP mechanics behind a narrow protocol: UID-only
    commands, read-only `SELECT`, `BODY.PEEK[]`, `UIDVALIDITY`, and the `_MAXLINE`
    ceiling raised at import.
  - `smtp_client.py` — the blocking SMTP mechanics: STARTTLS/SSL/plain, with **no
    plaintext fallback** (a failed upgrade aborts the send).
  - `mime.py` — inbound parse (RFC-2047 header decoding, `text/plain` preference, HTML
    stripped to text, quoted-history trimming, `parseaddr`-only sender addresses) and
    outbound build (`Message-ID` / `In-Reply-To` / `References`).
  - `transport.py` — the IMAP poll loop, the self-message filter, trust-seam
    integration, code-in-reply pairing, and session routing.
  - `delivery.py` — the `ChannelDelivery` the gateway delivers results through, plus the
    persisted thread state that keeps replies in one conversation.
  - `settings.py` — the app's own non-secret config + its credential keys.
  - `inbound_tap.py` / `trigger_source.py` — the shared inbound stream and the
    automation-event source built on it (see below).
  - `writes.py` — the live-writes kill switch this channel honors (see below).
- `mail_desk_setup.py` / `mail_desk_doctor.py` — the app's `gideon setup` / `doctor` hooks.
- `test_mail_desk_provider.py` + `tests/` — the app's own tests (no network, no sleeps,
  no writes outside a tmp home).

It imports core **only** via the Gideon **SDK** (never core internals), so core can evolve
without breaking it:

- `gideon.sdk.channel` — the transport ABC, `ChannelMessage`, the sender-trust seam
  (`guard_inbound`, `redeem_pairing_code`, `is_tracked_channel`), redaction, `run_chat`,
  `ProviderSettings`, `AppConfig`, `atomic_write`.
- `gideon.sdk.util` — `app_data_dir` (the UID cursor + thread state).
- `gideon.sdk.cli` — `SetupContext` / `DoctorLine`.
- `gideon.sdk.trigger_source` — `TriggerSourceProvider`, `SourceEvent`.

**No vendor SDK and no new dependencies:** `imaplib`, `smtplib` and `email` are stdlib.
The manifest declares no `pythonDependencies`.

## Automations from inbound traffic

This bundle also registers a **`trigger_source`** provider
(`mail_desk_runtime.trigger_source:create_provider`), so a message that arrives here can
fire a `kind: event` automation. Event names are namespaced by core from the app name:

- `app:gideonai-mail-desk:mail_received` — mail arrived from an allowed correspondent.
  One event, not two: every mail is a direct message (there is no room concept), so there
  is no second structural fact to name a second event with.

Author a trigger with pattern `AppEvent` and an `event_glob` matching one of those (or
`app:gideonai-mail-desk:*` for any of them). The quote-stripped new prose of the mail
arrives as the payload, **fenced at origin** by core; `meta` carries identifiers only
(`channel_id`, `sender`, `is_dm`).

Three things this deliberately does *not* do:

- **It observes nothing your trust gate refused.** The publish happens after core's
  guarded door returns `allowed` — and a `From` address is trivially forged, which is
  exactly why that decision is core's. A denied sender gets no session and arms no
  automation.
- **The event name never comes from the message.** It is chosen in code from the frozen
  list above by a structural fact, so a sender cannot pick which of your automations runs.
- **Prose never lands in `meta`.** `meta` is matched, not narrated, and core does not
  fence it — so neither the `From` display name nor the **`Subject`** is there. That does
  mean a subject-matching trigger is not expressible today; the limit is recorded rather
  than traded for the fence.

## Install

From the App Store, add the apps directory as a **local source**, then install **Mail
Desk** — the install runs through the security scanner and lifecycle exactly like any
other app. (Or `POST /api/apps {"source": ".../gideonai-mail-desk"}`.)

## Mailbox setup

1. **Dedicate a mailbox.** Use a fresh address, not your personal inbox: every message
   from a paired sender becomes a conversational turn.
2. **Create an app password** — never your account password:
   - **Gmail** — Google Account → Security → 2-Step Verification → App passwords → Mail.
   - **Fastmail** — Settings → Privacy & Security → App Passwords → New, scoped to
     *Mail (IMAP/SMTP)*.
   - **iCloud** — appleid.apple.com → Sign-In and Security → App-Specific Passwords.
3. Run `gideon setup` and pick your provider (hosts and ports are prefilled), or fill the
   Configure form and add the passwords with `gideon setup`.
4. Run `gideon doctor` — it performs the live **login + SELECT** probe on IMAP and a login
   probe on SMTP, so a wrong folder or SMTP port fails loudly rather than silently.

## Settings

| Key | Label | Notes |
|---|---|---|
| `imap_host` / `imap_port` / `imap_user` / `imap_use_ssl` | IMAP | Inbound. 993 + SSL by default. |
| `folder` | Folder | Polled **read-only** — your mail is never marked read. |
| `smtp_host` / `smtp_port` / `smtp_user` / `smtp_security` | SMTP | Outbound. 587 + STARTTLS by default. |
| `address` | Mailbox Address | Sends as, receives at, and anchors the self-message filter. Defaults to the IMAP login. |
| `poll_secs` | Poll Interval | 60s default, clamped to 10–3600. |
| `dm_activation` | Inbound Activation | `always`, or `off` to keep outbound delivery only. |

Secrets are **not** settings: the IMAP/SMTP passwords live in the shared credential store
under this app's own `MAIL_DESK_IMAP_PASS` / `MAIL_DESK_SMTP_PASS` keys. A blank
`MAIL_DESK_SMTP_PASS` reuses the IMAP one (one app password usually covers both).

## The live-writes kill switch

Handing a message to an SMTP relay is the least reversible write this app makes — once the
server accepts it there is no recall, no edit and no delete — so this transport honors the
platform's process-wide `GIDEON_DISABLE_LIVE_WRITES` switch, the same one core applies to
non-GET egress and local-model deletion. (This is the platform guard, checked after this
app's own SMTP configuration gate: an unconfigured mailbox reports a plain `False` because
it could not have written anything, and only a transport that WOULD have transmitted
reports a refusal.)

With the switch set, `send()` transmits nothing and returns a **typed refusal**
(`SendRefused`) instead. It is falsy, so every existing "did it send?" caller keeps
reading "not delivered" unchanged, but a caller that cares can tell a suppressed write
from a failed one with `isinstance(result, SendRefused)` — the two demand opposite
responses, and a bare `False` would conflate them.

Parsing follows the platform's fail-safe rule exactly: an **absent** variable allows
writes (the switch is opt-in), an explicit `0`/`false`/`no`/`off` turns the guard off, and
**any other present value — including a typo — turns it on**.

## Trust and pairing

Who may talk is owned by the **core sender-trust seam** (`channel_trust`, provider
`mail-desk`) — this app keeps no allowlist of its own.

1. An unknown address gets one canned reply asking for a pairing code (and you get one
   owner notification, deduped for 24h).
2. Run `gideon pair mail-desk` for an 8-digit code (TTL 10 min, single use).
3. They **reply with the code anywhere in the body** — quoting and signatures are fine.
4. From then on they converse; each thread gets its own session.

Trust is keyed on the address parsed out of `From`, never on the display name. A message
whose display name reads `allowed@example.com` but whose actual address is
`evil@attacker.test` is denied.

## Capabilities

| Capability | Value | Why |
|---|---|---|
| `inbound` | ✅ | the IMAP poll loop |
| `threads` | ✅ | `Message-ID` / `In-Reply-To` / `References` chains |
| `attachments` | ✅ | `upload_attachment` adds a MIME part |
| `rich_text` | ✅ | `deliver_rich` sends an HTML alternative |
| `reactions` | ❌ | mail has no reaction concept |
| `typing_indicator` | ❌ | nothing to show between messages |
| `edits` | ❌ | **this is how `streaming=false` is declared** — see below |

**Streaming is deliberately absent** (the C3 table marks the streaming trio MUST-NOT here:
a "live-updating message" would mean one mail per token). `ChannelCapabilities` has no
`streaming` field, and in every other channel a stream *is* a repeatedly-edited message —
so `edits=False` carries that meaning, and `start_stream()` returns `""` with no-op
append/stop. Core's mirror path already treats `""` as "this channel cannot stream". Both
halves are asserted together in `tests/test_mail_desk_transport.py::TestCapabilities`.

Approvals arrive as a **reply token**: the prompt mail carries `APPROVE <token>` /
`DENY <token>`, and only a reply from an already-allowed sender can resolve one. Both the
verb and the token must be present, and an explicit `DENY` wins over a body containing
both.

## Deferred, on purpose

- **IMAP IDLE.** `imaplib` has no IDLE support, so it would mean hand-rolling the command
  plus its 29-minute re-issue cycle and dead-connection detection. IDLE is "optional
  later"; the 60s poll cadence is configurable in the meantime.
- **OAuth2 / XOAUTH2** (DISCOVERY). Every provider documented above issues per-application
  passwords precisely for clients like this, and they need no token refresh, no client
  registration, and no browser round-trip in a headless gateway. OAuth2 would add a
  per-provider registration story and a refresh-token lifecycle before it improved
  anything; when a provider we care about drops app passwords, it becomes worth building.
- **Digest delivery target.** `deliver_notification` is the hook plan 42's notification
  rules use for a `channel_dm` / digest target; the core rules engine's `channel_dm`
  target has no dispatcher wired yet, so nothing in this app needs to change when it
  lands.

## License

MIT — see `LICENSE`.

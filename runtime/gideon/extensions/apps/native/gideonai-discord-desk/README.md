# Discord Desk

Discord bot messaging and inbound events for Gideon. The [manifest](app.json) registers a channel transport and trigger source; implementation lives in `discord_desk/`. Gateway and REST protocols use the declared Python libraries rather than a Discord vendor SDK.

## Configuration

Configure the bot token, public application ID and DM activation mode. Message-content access depends on the application's enabled Discord intents; an online connection alone does not establish readable inbound messages. Review the service's current application settings and scopes when creating or changing the bot.

Sender pairing and tracked guild channels use Gideon's channel-trust seam. Untracked channel content does not become an unrestricted conversational turn. Direct-message activation supports `always`, `mention` and `off`; that choice does not replace sender admission.

## Events and delivery

The trigger source publishes `app:gideonai-discord-desk:direct_message` and `app:gideonai-discord-desk:guild_message` after admission. These event names are selected by code, not by message text. The payload remains untrusted external content, and execution requires the trigger's actual authority.

Delivery handles message splitting, edit streaming, reactions and approval interactions. REST rate-limit handling and Gateway heartbeat/reconnection logic are source behavior, not evidence that a particular token or guild setup works.

## Installation and authority

This bundle is included in the native app catalog. Configure it through Gideon's Apps and Channels surfaces. Installing an external copy still requires the normal source review and consent flow; a local path or familiar display name is not approval.

Conversation admission, protected owner identity and permission to answer an approval are separate decisions. A paired or allowed sender does not automatically acquire owner authority. Review the actual configured channel trust and app grants before exposing inbound traffic.

The transport's `send()` checks `GIDEON_DISABLE_LIVE_WRITES`. A suppressed write returns a falsy typed `SendRefused`; distinguish this from an unconfigured or failed send. This guard is a transport contract, not a promise that every possible external write is intercepted.

## Verification limits

Bundle tests exercise selected protocol and trust paths. They do not prove that an external service accepts your credentials or that a live conversation, notification or approval completes. Validate the configured service separately and retain failures as failures.

## License

See [LICENSE](LICENSE) for this bundle's license.

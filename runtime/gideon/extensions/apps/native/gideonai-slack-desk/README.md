# GideonAI Slack Desk

Slack messaging, thread delivery and inbound events for Gideon. The [manifest](app.json) registers channel, inbox and trigger-source providers. Implementation lives in `slack_desk_runtime/` and imports the supported Gideon SDK facade.

## Configuration

The bot token supports outbound API calls; the app token is also required for Socket Mode inbound traffic. Store both using the app configuration/credential surface. Use the supplied Slack app manifest as a starting point and review requested scopes against the workspace you intend to connect.

Current runtime behavior is owner-only even though legacy settings include allowed-user and open-channel lists. Do not treat those fields as permission to grant another person owner access. When no owner is preset, the current event path can claim the first eligible human sender from a DM, mention or tracked channel; once set, that path does not transfer ownership. Verify the resulting owner identity before enabling unattended work.

Track intended channels and choose their activation modes. Direct-message posture is distinct from tracking a shared channel. Approval and configuration interactions recheck owner identity; a button identifier alone is not approval.

## Events and delivery

The trigger source publishes `app:gideonai-slack-desk:direct_message` and `app:gideonai-slack-desk:channel_message`. Message admission, activation and deduplication precede publication. Trigger execution still requires its own current grant and source authority.

The transport declares inbound messages, threads, attachments, reactions, edits, rich text and typing indicators. Declaration presence does not qualify every workspace configuration. Streaming delivery edits an existing Slack message; completion and error states remain distinct.

## Installation and authority

This bundle is included in the native app catalog. Configure it through Gideon's Apps and Channels surfaces. Installing an external copy still requires the normal source review and consent flow; a local path or familiar display name is not approval.

Conversation admission, protected owner identity and permission to answer an approval are separate decisions. A paired or allowed sender does not automatically acquire owner authority. Review the actual configured channel trust and app grants before exposing inbound traffic.

The transport's `send()` checks `GIDEON_DISABLE_LIVE_WRITES`. A suppressed write returns a falsy typed `SendRefused`; distinguish this from an unconfigured or failed send. This guard is a transport contract, not a promise that every possible external write is intercepted.

## Verification limits

Bundle tests exercise selected protocol and trust paths. They do not prove that an external service accepts your credentials or that a live conversation, notification or approval completes. Validate the configured service separately and retain failures as failures.

## License

See [LICENSE](LICENSE) for this bundle's license.

# Mail Desk

Two-way IMAP/SMTP conversations for Gideon. The [manifest](app.json) registers the channel and trigger-source providers. Implementation lives in `mail_desk_runtime/`. This is a conversational transport; it is distinct from a read-only mail inbox source.

## Configuration

Configure IMAP host, port, login, mailbox and TLS settings, then SMTP host, port, login and security mode. Passwords use the shared credential store under app-owned keys. The settings include mailbox address, polling interval and inbound activation posture. A dedicated mailbox helps separate agent traffic from unrelated mail.

IMAP polling uses UID-based reads and `BODY.PEEK[]`. SMTP supports the configured security mode; a failed requested TLS upgrade must not silently fall back to plaintext. Validate both receive and send configuration rather than inferring readiness from one successful login.

## Trust, events and delivery

Correspondent admission and pairing use Gideon's channel-trust seam. A parsed From address identifies the policy lookup; it is not cryptographic proof that a message came from that person. Review mailbox and sender-authentication assumptions before using email as an authority channel.

The trigger source publishes `app:gideonai-mail-desk:mail_received` after admission. Message bodies remain external content. Thread delivery uses Message-ID references. Mail does not provide reactions or token-by-token edited streaming; the capability declaration reflects those limits.

Approval replies contain explicit approval or denial tokens and require the actual permitted responder. Mail delivery is an external write, and successful SMTP acceptance does not establish final mailbox delivery.

## Installation and authority

This bundle is included in the native app catalog. Configure it through Gideon's Apps and Channels surfaces. Installing an external copy still requires the normal source review and consent flow; a local path or familiar display name is not approval.

Conversation admission, protected owner identity and permission to answer an approval are separate decisions. A paired or allowed sender does not automatically acquire owner authority. Review the actual configured channel trust and app grants before exposing inbound traffic.

The transport's `send()` checks `GIDEON_DISABLE_LIVE_WRITES`. A suppressed write returns a falsy typed `SendRefused`; distinguish this from an unconfigured or failed send. This guard is a transport contract, not a promise that every possible external write is intercepted.

## Verification limits

Bundle tests exercise selected protocol and trust paths. They do not prove that an external service accepts your credentials or that a live conversation, notification or approval completes. Validate the configured service separately and retain failures as failures.

## License

See [LICENSE](LICENSE) for this bundle's license.

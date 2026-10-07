# Gideon examples

These directories contain development examples. They are not automatically installed
or served by the gateway.

| Directory | Purpose |
|---|---|
| [app-template](app-template/README.md) | A tool-provider scaffold with a manifest, CLI contributions, and tests. Its provider deliberately exposes no tools until implemented. |
| [registry](registry/README.md) | An example app-listing format, schema, validator, and listing policy. Its sample URLs are placeholders, not a deployed catalogue. |

For a new provider, start with `gideon app new my-tool --type tool`, or copy the template
into your own project. Keep app imports on the public `gideon.sdk.*` surface and declare
only the capabilities and permissions your implementation uses.

An app bundle must be reviewed and installed through Gideon's app lifecycle before it
participates in the gateway. A listing or a scaffold does not grant trust, credentials,
or permission to run agent work. See the [app platform](../docs/architecture/APP_PLATFORM.md)
and [channel app guide](../docs/guides/BUILD_A_CHANNEL_APP.md).

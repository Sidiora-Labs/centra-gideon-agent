# Provider boundaries

Gideon separates shared runtime mechanisms from app-owned integrations. Core owns
contracts, registries, authorization, and orchestration. An app contributes vendor
endpoints, credentials, model catalogues, binary selection, transport behavior, and UI
through those contracts.

This is a dependency and lifecycle boundary, not an operating-system sandbox. App Python
code can run in the gateway process; subprocess isolation depends on the declared
program and actual sandbox provider. See [security](SECURITY.md).

## Source layout and imports

The runtime package is `runtime/gideon/`. Bundled integrations live under
`runtime/gideon/extensions/apps/native/`; the top-level `apps/` directory contains the
console and native shells. Other bundles come from configured sources or local paths.
No external app repository is assumed.

App code imports public contracts through `gideon.sdk.*`. These facades expose provider,
channel, model, settings, credentials, and other supported contributions without binding
apps to internal module locations. The separate HTTP client under `packages/python-client/`
is not a substitute for these in-process provider contracts.

## Shared mechanisms and app responsibilities

| Surface | Shared mechanism | App contribution |
|---|---|---|
| Models | OpenAI-compatible and Anthropic-compatible protocol clients, registry, capability and catalogue contracts. | Endpoint, model declarations, credential policy, discovery, and vendor-specific translation. |
| Media | Speech, image, video, embedding, and catalogue interfaces. | Supported models, vendor requests, local execution, and capability metadata. |
| Agent runtimes | ACP protocol and session lifecycle, permission routing, options and compaction contracts. | Executable resolution, launch declarations, dialect selection, and login instructions. |
| Search and knowledge | Provider contracts and binding resolution. | Vendor query behavior, indexes, authentication, and truthful result metadata. |
| Channels | Transport, delivery, trust, pairing, and approval contracts. | Connection, inbound receiver, formatting, attachments, and vendor links. |
| Automation | Trigger sources, action providers, consent, completion, and run lifecycle. | Declared events and actions backed by the integration. |
| App UI | Contribution loading, shell SDK, and authenticated backend routing. | Bundle-owned pages and interactions beyond generic settings. |

The accepted manifest provider types are defined in
`runtime/gideon/extensions/apps/manifest.py`. A supported type is not evidence that a
particular provider is installed, enabled, configured, or operational.

## Protocol code and reference data in core

Core contains protocol implementations reused by several vendors, including
`runtime/gideon/integrations/llm/openai.py` and
`runtime/gideon/integrations/llm/anthropic.py`. Apps select and configure them; a shared
HTTP message format does not imply a fixed vendor binding.

ACP dialects encode declared protocol variations. Model capability inference and pricing
contain fallback reference data; explicit provider declarations and actual runtime
selection remain important. Secret-detection patterns and existing credential key names
may mention vendors because they recognize or preserve actual credential formats.
Do not rename such security data merely to remove a vendor word.

`runtime/gideon/sdk/provider_helpers.py` provides `BrandedProviderSpec`, catalogue and
registration helpers for integrations that share a protocol. More specialized providers
can implement their own translation. Subscription credential sources declare readable
stores and login hints; reading a declared store does not authorize core to refresh or
rewrite it on the vendor's behalf.

## Resolution and lifecycle

Provider instances and use-case bindings determine which configured integration is
selected. Model resolution constructs clients through registered factories and evaluates
current capability, readiness, and model-level execution locality where supported.
A catalogue row is discovery metadata, not a successful inference result.

Local model management is a separate contract under
`runtime/gideon/integrations/local_models/`: download, residency, fit, deletion, and
metadata need to reflect the provider's real files and execution behavior. A model absent
from a catalogue must not acquire capabilities merely because a name was guessed.

App loading records code provenance through
`runtime/gideon/extensions/apps/code_provenance.py`. Logger names and manifest
`loggerRoots` do not grant authority to claim another module's output. Contributions
also require lifecycle ownership: disable, uninstall, failed loading, and replacement
must withdraw the owning app's registrations without deleting a newer replacement or a
core contribution.

Runtime authorization is separate from discovery. Declared app tiers and live grants
constrain work and descendants. An app label supplied in a request cannot create
app authority; trusted host origin, current installation, and consent remain required.

## Contributor guidance

Add vendor behavior in an app using an existing public seam. If the seam is missing,
design a generic contract and its consumers before advertising support. Keep settings,
credential access, logging attribution, and unload behavior attached to the same app
identity. Preserve explicit refusals and unavailable states instead of presenting them
as successful execution.

The bundled Slack app illustrates channel-owned transport and rendering; see
[inbox and channels](INBOX_CHANNELS.md) and
[the channel guide](../guides/BUILD_A_CHANNEL_APP.md). See
[the app platform](APP_PLATFORM.md) for manifest review, programs, permissions, and SDK
boundaries.

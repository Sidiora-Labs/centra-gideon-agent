# Widgets and host actions

Gideon supports iframe widgets and registered GenUI components. They have different
rendering and trust boundaries. A widget's appearance or message must not independently
grant tools, workflow authority, or access to another conversation.

## Iframe boundary

HTML and React widget frames use script-enabled sandboxes without same-origin access.
The constructed document applies a restrictive content-security policy. Those controls
separate the frame from host DOM, cookies, and storage and restrict its direct network
behavior; inspect the actual CSP and browser enforcement rather than treating a sandbox
attribute as a complete execution audit.

The host message parser is
`apps/console/src/shared/ui/widget/useWidgetActionBridge.ts`. It first requires
`event.source` to be the actual frame's `contentWindow`, then validates the message
shape. Current messages include sizing, readiness, rendering errors, actions, edit values,
edit readiness, and annotations. Unknown types and invalid fields are dropped or surfaced
according to that parser; not every error field is rejected rather than normalized.

Frame identity proves which frame sent a message. It does **not** prove that a human
clicked: arbitrary code inside the same script-enabled frame can send its own messages.
The injected action/annotation handlers check trusted browser gestures, but that check
is not an independent host-verifiable authorization for every message the frame can emit.
Treat frame payloads as untrusted and preserve runtime authorization at consumers.

## Action routing

Supported actions become bounded `[UI]` conversation text through
`apps/console/src/shared/ui/widget/actionTurn.ts`. Payload serialization failures are
refused, and text is capped at 16 KiB with visible truncation. Being represented as text
does not make the payload trusted model instruction or grant permission to execute it.

The bridge selects a mounted consumer by priority. Chat consumes actions in its selected
conversation; the shell fallback opens chat and transfers pending text in memory with a
short expiry. The transfer is not an executable `?send=` URL. React iframe hosts can
restrict forwarding to sizing/error behavior rather than exposing an action bridge.

Validate session ownership and current work constraints at the actual request handler.
A widget field naming an app, run, tool, or parent session cannot replace host identity.

## GenUI

GenUI uses registered components in the host React tree rather than arbitrary iframe
scripts. `apps/console/src/shared/ui/genui/actions.ts` builds model-facing action content
and optional human-facing labels. Host context selects the producer: chat, a workflow
gate, or a dashboard tile. Model-authored block attributes must not choose a different
run's gate token or another tile's authority.

Workflow and tile actions use their actual endpoint consumers and consent checks. The
tile path is `runtime/gideon/interfaces/dashboard/tile_actions.py`; a rendered button
must not skip the saved binding's capability fence or unattended admission.

## Surface layers and recovery

Core, app, and user/agent layers compose through registrations. A higher layer can add a
component name; it cannot silently replace a lower layer's registered identity.
Error boundaries preserve a named failure rather than collapsing the whole surrounding
surface.

The layer ceiling is implemented in
`apps/console/src/shared/ui/surfaces/layers.ts` and
`runtime/gideon/workspace/surface_layers.py`. Safe mode selects core surfaces only,
through the supported URL/launch configuration. This is a UI recovery mechanism, not a
reversal of already executed app code or external effects.

## Artifact iteration

Edit messages apply/read validated CSS custom properties; annotation messages carry
bounded element context. The host must verify the expected frame and validate those
fields. Live preview edits, saved changes, and requests for an agent to regenerate an
artifact are distinct actions.

`apps/console/src/shared/ui/widget/useArtifactIteration.ts` owns the parent integration.
Corrections route through chat or the actual loop steering consumer according to the
host. They retain the action text cap and current runtime authority; a correction does
not itself mean the artifact was rewritten.

Inspect `widgetSrcdoc.ts`, `WidgetFrame.tsx`, `ReactWidgetFrame.tsx`, `editMode.ts`, and
`annotate.ts` under `apps/console/src/shared/ui/widget/` for the current child documents,
handlers, and limits. Source validation and focused tests do not establish visual,
keyboard, screen-reader, or browser containment behavior on every target.

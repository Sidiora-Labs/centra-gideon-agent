# Gideon assistant shell

Gideon opens in a calm, chat-first application with a recognizable assistant header and five labelled destinations: Chat, Activity, Ideas, Goals and Apps. The shell owns navigation, layout and visual continuity. Gideon's existing records and feature modules supply the content; a destination never invents its own copy of a conversation, task or app.

The browser application starts from the actual Expo/React Native Web screen structure retained in the proposed `apps/assistant/` package. The existing console remains reachable during migration. Shell routes and module contracts let trusted Gideon feature pages join the same browser application without importing the console's application entry or mounting a second main shell. Implementation begins in the OSS application; distribution-specific bootstrap belongs to `delivery`.

## Sequential implementation tasks

- [ ] **shell-01 — Define the shared destination and frame contract.**
  - Create `apps/assistant/src/shared/shell/shellRoutes.ts` and `apps/assistant/src/shared/shell/WorkspaceFrame.web.tsx` as proposed files.
  - Give Chat, Activity, Ideas, Goals and Apps stable route IDs, labels, location serialization, and a typed way to open a destination, detail or workspace with source record ID, session ID and return context.
  - Define the frame's route, title, actions, content, loading/error, width, scroll, focus target and back behavior without coupling it to one feature's data model.
  - Make route payloads serializable and versionable so browser navigation can restore them after refresh.
  - Require the source record type and ID when opening a detail; never infer a target from a display label.
  - Reserve a full-width, full-height workspace mode for editors, diagrams, terminals and other complex tools; keep the centered reading width for conversation and compact views.
  - Define the `open`, `back`, `close` and `returnToChat` callbacks once for all feature families.
  - Acceptance: a feature can link to a precise workspace record and return to its previous destination, selection and scroll position. Unknown or malformed routes resolve to a labelled recovery view with a safe Chat action.
  - Dependencies: none. `conversation`, `activity`, `discovery` and other feature foundations consume this contract; `delivery` supplies the separate identity/bootstrap contract.

- [ ] **shell-02 — Retain the real assistant application shell under Gideon identity.**
  - Add the proposed `apps/assistant/package.json`, `apps/assistant/App.tsx`, `apps/assistant/src/ui.tsx` and `apps/assistant/src/shared/shell/ShellIdentity.tsx` from the selected application presentation, preserving required source attribution in `apps/console/public/assistant-source-notices.txt` when source is copied.
  - Preserve the assistant header, avatar/name/status composition, conversation-first screen geometry, rounded controls, cards, sheets and bottom navigation treatment. Replace sample identity and access-key copy with Gideon identity obtained through the `delivery` bootstrap.
  - Drive the header's status from real identity, task and approval state. Mark unavailable status as unknown rather than implying that an idle agent is working.
  - Keep the conversation screen as the default visual body and leave its data/controller seam to `conversation`.
  - Keep the shell visible only when identity is ready; show actionable loading, sign-in and recoverable error states according to the real bootstrap outcome. Do not show sample tasks, notification counts or a static online status as customer state.
  - Keep primary controls usable while noncritical counts or summaries refresh.
  - Acceptance: opening the assistant entry shows a Gideon-branded chat-first shell with truthful identity and no unrelated credential prompt; a failed bootstrap offers the correct retry or sign-in route.
  - Dependencies: shell-01 and `delivery` bootstrap.

- [ ] **shell-03 — Make navigation clear on desktop, tablet and phone.**
  - Update proposed `apps/assistant/App.tsx`, `apps/assistant/src/shared/shell/ShellNavigation.tsx` and `apps/assistant/src/shared/shell/shellRoutes.ts`.
  - Display all five destination labels in the primary navigation. Keep Chat the default home. Make current location and unread or pending counts understandable without relying on icon shape or color.
  - Mark the selected destination to assistive technology and expose a descriptive name for the conversation/menu and notification actions.
  - Use a compact assistant rail or header on wide screens, a labelled bottom bar on narrow screens, and a clearly labelled menu for secondary areas. A long destination title, translated label or large text setting must not clip navigation or hide actions.
  - Maintain touch targets, visible focus and a predictable keyboard traversal order in either layout.
  - Preserve a visible path from every destination to Chat and Apps. Opening a secondary workspace or detail must retain its parent and the current conversation return context.
  - Keep secondary area labels meaningful; discovery owns its searchable catalogue and pins.
  - Acceptance: keyboard, pointer and touch users can locate each of the five destinations; changing viewport width preserves the active destination and does not create overlapping navigation.
  - Dependencies: shell-02.

- [ ] **shell-04 — Make routes and browser history durable.**
  - Add proposed `apps/assistant/src/shared/shell/routeState.web.ts`, `apps/assistant/src/shared/shell/routeState.web.test.ts` and `apps/console/src/app/shell/assistantRouteBridge.ts`.
  - Read and write same-origin assistant URLs for destinations, records and session context; use browser Back/Forward as a navigation source rather than local section state alone. Map existing console links to their corresponding assistant destination when available.
  - During migration, a labelled existing-console route may carry a user to a feature that has not moved. Preserve the destination and return location across that handoff.
  - Distinguish a moved assistant route from an existing-console handoff in link text and browser history.
  - Validate route parameters before use. A deleted or inaccessible record shows an honest unavailable state and a return action; refresh and reconnect restore the same valid route without duplicating a request or losing the draft.
  - Preserve query and fragment semantics needed by existing conversation and workspace links.
  - Avoid putting identity secrets or unsent message text into the URL.
  - Acceptance: a deep link, refresh and Back/Forward all open the same intended destination with correct selected state. An unsupported URL never displays an unrelated feature as if it were the target.
  - Dependencies: shell-01 and shell-03; route entry and auth behavior come from `delivery`.

- [ ] **shell-05 — Preserve the assistant visual language across themes and overlays.**
  - Add proposed `apps/assistant/src/shared/shell/shellTheme.web.ts`, `apps/assistant/src/shared/shell/shellTheme.web.css` and `apps/assistant/src/shared/shell/ShellOverlayRoot.web.tsx`; integrate existing `apps/console/src/shared/theme/tokens.css` only through a scoped bridge for trusted feature pages.
  - Support light, dark and system themes while retaining the assistant's card, typography, spacing, focus and status hierarchy. Keep contrast, focus rings, reduced motion and high zoom usable.
  - Follow system theme changes while the app is open unless the user chose an explicit preference.
  - Carry directionality and localization through the shell and trusted module host.
  - Give dialogs, sheets, menus, toasts and popovers an owned overlay root and stacking order. Avoid global CSS resets that alter the assistant screen or leave nested feature pages without their required tokens.
  - Prevent hidden background actions while a modal surface owns focus.
  - Acceptance: changing theme during an open sheet retains legible text and focus; a feature dialog appears above the shell and returns focus to its opener when closed.
  - Dependencies: shell-02.

- [ ] **shell-06 — Build the responsive workspace frame.**
  - Complete proposed `apps/assistant/src/shared/shell/WorkspaceFrame.web.tsx`, `apps/assistant/src/shared/shell/workspaceFrame.web.css` and `apps/assistant/src/shared/shell/WorkspaceFrame.web.test.tsx`.
  - Let compact reading surfaces use the centered conversation width while trusted workspaces consume available width and height. Provide independent scroll ownership, safe-area and mobile keyboard handling, resizable panels where a feature needs them, and a stable header/back affordance.
  - Let a module request a documented width mode; do not make each feature override shell CSS.
  - Keep scroll position per route when moving between a workspace and Chat.
  - Use sheets for short phone details and full-screen routes for editors and large tools. Surface explicit loading, permission, empty and failed-module states with a retry or return action; never translate a load error into an empty workspace.
  - Honor reduced motion when resizing panels or switching workspace modes.
  - Acceptance: a representative editor or graph fits desktop and tablet without the chat width cap; on phone its supported actions remain reachable without horizontal page overflow.
  - Dependencies: shell-01, shell-04 and shell-05.

- [ ] **shell-07 — Define and prove trusted web module loading.**
  - Add proposed `apps/assistant/src/shared/shell/webModules.web.tsx`, `apps/assistant/src/shared/shell/webModules.native.tsx` and `apps/assistant/src/shared/shell/webModules.web.test.tsx`.
  - Register trusted Gideon feature modules by stable route ID and load them lazily inside the shell's route, identity, theme, overlay and error boundaries. Import feature components below `apps/console/src/app/bootstrap/main.tsx`; do not import that full bootstrap as a page.
  - Define module props for route params, authorized user context, navigation, return context and host services.
  - Require a real permission outcome before rendering a privileged feature, with an actionable unavailable state when access is denied.
  - Establish one compatible React dependency graph before sharing contexts. Demonstrate one rich result/detail and one representative complex DOM module with its styles, worker and keyboard/focus behavior in the browser entry.
  - Preserve document isolation for contributed app UIs and generated previews while the shell hosts trusted modules directly.
  - Native resolution must not import browser-only editors, CSS or workers. It returns an explicit supported handoff or unavailability action for a web-only route; native product parity is separate delivery work.
  - Acceptance: a user opens the representative module, interacts with it and returns to the same session and scroll context; module load failure can be retried without restarting the shell. Untrusted app content remains in its existing isolated host.
  - Dependencies: shell-06 and `delivery` bootstrap; feature modules may add entries as their own specs complete.

- [ ] **shell-08 — Qualify the shared shell journey.**
  - Add proposed `apps/assistant/src/shared/shell/shellJourney.web.test.tsx` and `apps/assistant/e2e/shell-navigation.spec.ts` after the real entry and route bridge exist.
  - Exercise identity loading and recovery; all five labelled destinations; a record deep link; refresh, Back/Forward and return context; light/dark themes; overlay focus; and compact/full-width layouts at desktop, tablet and phone sizes.
  - Include the boundary case of a deleted record and a module load failure in the navigation journey.
  - Include keyboard navigation, large text, reduced motion and mobile keyboard overlap.
  - Use real route/provider implementations and representative reachable module content. Record any destination that still hands off to the existing console as migration coverage, with its path and return behavior.
  - Compare the assistant header, navigation, cards and sheets against the retained presentation after wiring live Gideon state.
  - Acceptance: the shell journey meets the preceding contracts without fake customer data, inert controls or a second main navigation tree. The gate records observed browser behavior and any remaining feature-family gaps accurately.
  - Dependencies: shell-02 through shell-07; destination data and distribution qualification remain owned by their feature specs.

## Shared integration boundaries

`conversation` owns the Gideon transcript and session controller behind Chat. `activity`, `personal` and `discovery` own Activity, Ideas/Goals and Apps content. `delivery` owns the OSS entry, authentication/bootstrap and packaging. The shell owns their common route and visual frame, so a feature can land without redefining top-level navigation.

The browser-first shell keeps trusted feature modules in one React tree. Existing isolated app and preview surfaces remain separate document boundaries reached through a typed route. Route ownership, focus restoration and error recovery must hold across either path.

The stable five destination IDs belong to the shell contract. Feature teams can add secondary routes and contextual launch actions through that contract while keeping the primary navigation readable.

Status, counts and destination content always come from Gideon's authoritative records. The shell presents unavailable or loading state when those records cannot be read.

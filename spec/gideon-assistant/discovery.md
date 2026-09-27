# Gideon Assistant discovery and Apps

Purpose: make Gideon's capabilities easy to find from a compact assistant interface while retaining real app, settings, and workspace journeys.

The Apps destination is a labelled catalog with search, categories, pinned items, recent destinations, and clear launch actions.
It shares the shell's route and return-context contract with Chat, Activity, Ideas, and Goals.
Large tools open in the shared workspace frame; short details may use a sheet on small screens.
An inventory placement describes where a view belongs. It does not establish that its route or action currently works.

## 1. Establish the destination registry

- [ ] Create a typed 214-entry placement registry in `apps/assistant/src/features/discovery/destinations.ts`.
  Give every entry a stable ID, user-facing label, category, canonical route, owner family, workspace mode, and availability state.
  Reconcile every entry against the actual router and feature module before setting its availability to ready.
  Keep unverified or incomplete entries visible with an accurate next step; never label inventory presence as a working route.
  Record whether a destination opens a compact detail, full workspace, settings section, or external app UI.
  Reject duplicate IDs, missing destinations, broken parent links, and category-less entries in a focused registry check.
  Keep migration aliases and query/subview preservation in the shell route contract.
  The 214 placements divide into Chat 8, Work 34, Create 43, Library 25, Personal 47, Inbox 2, Apps 13, Settings 38, and Getting started 4.
  Use `spec/gideon-assistant/destinations.md` as the public placement checklist for the 214 IDs and their feature owners.
  Treat those group names as inventory buckets; use the checklist's per-route owner rather than assigning ownership from a group.
  Chat includes Work-owned Rooms; Work includes Code-owned views.
  Library includes Code-owned files and artifacts, plus Personal-owned Ideas and Journal.
  Personal includes Communications-owned views.
  Discovery provides Apps and settings/platform navigation; Delivery provides onboarding.
  Keep each placement's category and feature owner as separate fields in the registry.
  A placement can be discoverable before its family is implemented, but its status and next action must say so.
  Do not count a matching route string alone as proof that the destination works.
  Acceptance: all 214 IDs have a declared placement and verification state, with no invented claim of route readiness.

## 2. Build the searchable Apps index

- [ ] Adapt the Apps presentation in `apps/assistant/src/features/discovery/AppsScreen.tsx` to show a calm labelled index.
  Keep named categories rather than one grid of 214 unrelated tiles.
  Search labels, aliases, categories, and short descriptions across all placement entries and installed apps.
  Show category and destination type beside each result so similar names remain distinguishable.
  Let users filter by category, installed status, and availability without hiding unavailable entries silently.
  Results must identify a useful primary action: open, connect, configure, install, or inspect availability.
  Empty search explains what was searched and offers a clear reset; loading and retrieval errors have retry actions.
  The index must work with keyboard, screen reader names, visible focus, and responsive desktop/tablet/phone layouts.
  Acceptance: a user can find any registered placement or installed app by its label and understand its next action.

## 3. Make the 17 named miniapps actionable

- [ ] Carry the named collection into `apps/assistant/src/features/discovery/miniapps.ts` and `MiniappCollection.tsx`.
  Include Research, Slides, Studio, Writer, Music, Worlds, Knowledge, Journal, Health, People, Compass, Automations, Code, Agents, Lab, Workspace, and Connections.
  Each card needs a plain description, category, status, and a useful first action tied to a canonical destination.
  Research opens reports; Slides opens production; Studio opens images; Writer opens writing; Music opens repertoire.
  Worlds opens stories; Knowledge opens the knowledge workspace; Journal opens journals; Health opens wellbeing.
  People opens communications; Compass opens goals; Automations opens workflows; Code opens code.
  Agents opens agents; Lab opens experiments; Workspace opens working environments; Connections opens connection management.
  Reconcile each target with the route registry and the responsible feature owner before claiming it is ready.
  A pending target describes the available route or setup action and the work still needed.
  Acceptance: all 17 appear by name, have stable destinations, and launch a real first action when that family is ready.

## 4. Preserve pins, recents, deep links, and return context

- [ ] Add persistent, user-scoped pin and recent-destination state in `apps/assistant/src/features/discovery/discoveryState.ts`.
  Pin and unpin from cards and results; recents update after a successful launch, without recording failed routes as visits.
  Retain active category, search, scroll, and selected record when returning from a workspace to Apps.
  Open direct links to the intended destination and preserve relevant subview, query, and record identity.
  Respect browser back/forward and the shell's return-to-Chat or return-to-Activity action.
  Do not expose a sensitive record title in shared global recents where its privacy context forbids it.
  Resolve stale pinned destinations into a visible unavailable state with remove or recovery actions.
  Keep keyboard focus on the correct result or launch control after returning from a detail or error.
  Acceptance: pins survive reload, recents reflect successful launches, and deep links restore the same destination context.

## 5. Integrate installed apps and their host

- [ ] Connect `AppsScreen.tsx` to the existing installed-app and catalog APIs through `apps/assistant/src/features/discovery/appAdapter.ts`.
  Distinguish a named Gideon miniapp from an installed extension, a personal connection, and a provider or model.
  Preserve app enable, configure, update, remove, and installation journeys with real permissions and consent.
  Reuse the app host, manifest page declaration, app frame, and app capability boundary in `apps/console/src/features/apps/`.
  Add a web bridge in `apps/assistant/src/features/discovery/AppHost.web.tsx` for contributed UI pages.
  Keep generated or contributed content inside its existing isolation boundary and explain denied capabilities.
  Show installed, disabled, updating, absent, disconnected, permission denied, and load failure states accurately.
  Failed installations and host loads offer retry or safe recovery without claiming that an app is ready.
  Acceptance: a permitted installed app opens its real UI and returns to Apps with its state intact.

## 6. Keep settings and connection configuration discoverable

- [ ] Add a labelled settings index in `apps/assistant/src/features/discovery/SettingsIndex.tsx`.
  Cover all 38 settings placements and preserve direct links to their existing sections.
  Group account, appearance, chat, voice, models, providers, search, apps, privacy, diagnostics, and operations clearly.
  Separate user preferences, personal account connections, installed app settings, and managed service configuration.
  Use the real configuration APIs and preserve revision, validation, error, retry, and reconnect feedback.
  A user should know whether a setting is editable, unavailable, read only, or controlled by another owner.
  Never reveal credential values in search results, previews, or recents.
  Preserve a route back to the parent settings category and the source conversation or app when applicable.
  Acceptance: all 38 settings entries are searchable and their ready sections open at the intended state.

## 7. Wire contextual discovery and qualify each placement

- [ ] Let chat results, Activity records, notifications, and workspace artifacts launch registered destinations by ID.
  Add `apps/assistant/src/features/discovery/openDestination.ts` as the shared launch contract.
  Require source record IDs, route parameters, and return context so a user arrives at the relevant item.
  Reconcile each of the 214 registry entries as feature families deliver their real workspaces.
  Record route existence, actual useful action, direct-link reload, and return navigation separately.
  Keep entries pending until their owner family supplies a working destination and observed user journey.
  Include desktop, tablet, and phone geometry; use full-screen mobile routes for tools that need room.
  Cover empty, unavailable, permission denied, offline, stale, and recovery states with truthful labels.
  Verify keyboard traversal, screen reader labels, focus restoration, and no silent route failures.
  Acceptance: every placement is accounted for; ready entries pass a real launch-and-return journey, and pending entries remain explicit.

Dependencies: shell routing and workspace frame; delivery authentication/bootstrap; family destinations as conversation, activity, personal, communications, work, code, library, studio, and browser become available.

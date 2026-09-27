# Personal implementation specification

Gideon's Ideas and Goals destinations should make personal decisions and progress easy to understand. Health, Journal, memory, and relationship context open dedicated spaces when they need more room or stronger privacy controls. Canonical Gideon records remain the source of truth throughout.

The assistant should present a short next step on compact screens, then open the full source record for decisions and edits.
Personal information stays scoped to the signed-in account and follows each source's access rules.
Linked work remains evidence for personal progress, with the user retaining control of human milestones.

## Sequence and dependencies

Work follows the shared `shell` route and workspace contract, the `delivery` identity bootstrap, and the `activity` source-link contract. `discovery` supplies searchable Health, Journal, Memory, and People entry points. `communications` owns People records and relationship actions; this feature links to those records without copying them. All work belongs to global wave 4.

- [ ] **personal-01 — Establish personal routes and typed data access.**
  - Add named reads and writes for Ideas, human Goals and plans, memory, Health, and Journal under `apps/assistant/src/features/personal/client.ts` and `routes.ts` (proposed paths).
  - Use account-scoped, canonical record IDs and source kinds; retain expected revisions and request IDs for writes.
  - Scope every list and detail request to the active account and clear cached personal data when that account changes.
  - Resolve links by source kind and ID so a journal entry, health record, or goal opens the correct detail.
  - Open Ideas and Goals as the two main destinations. Open Health, Journal, and Memory in focused workspaces from Apps and contextual links.
  - Preserve the selected detail, draft, and return destination when moving among Chat, Activity, and personal views.
  - Share the shell's theme, focus, and route patterns, including readable narrow-screen headings and visible back actions.
  - Show separate loading, empty, unavailable, stale, and failed states; provide a retry that refreshes the same selection.
  - Acceptance: a signed-in user can reach every personal destination and return to the same conversation without losing context.

- [ ] **personal-02 — Project evidence-backed Ideas.**
  - Build `apps/assistant/src/features/personal/IdeasScreen.tsx` around source-specific recommendations and existing proposal records.
  - Each actionable card shows its reason, evidence title and source, proposed action, source freshness, and current decision status.
  - Group recommendations by actionability and recency while retaining a way to see the full decision history.
  - Open evidence through the authorized source route; a card must not copy sensitive source bodies into general lists.
  - Keep lightweight chat prompts visually separate from actionable Ideas; prompts do not imply a stored decision.
  - Give an unsupported or revoked evidence source a clear unavailable label without exposing protected content.
  - Keep accepted and dismissed items accessible through status filters and stable deep links.
  - Announce refreshed, empty, and partially unavailable results without changing the active filter unexpectedly.
  - Acceptance: reloading Ideas preserves source identity and decisions; missing evidence never becomes a fabricated citation.

- [ ] **personal-03 — Complete the Ideas decision lifecycle.**
  - Reuse the originating proposal's native decision contract when it already supports accept and dismiss.
  - Add a minimal recommendation record only for eligible kinds without a durable decision owner; keep evidence references and decision history.
  - Record the owner account, source kind and ID, recommendation revision, action target, and decision timestamp.
  - Present the action being authorized in plain language before creation and keep the edited prompt visible through review.
  - Let the user revise the proposed task prompt before acceptance, then create or link the real task exactly once.
  - Store the accepted task ID and expose a direct route to its Activity detail; dismissal stores a durable decision without creating work.
  - Confirm the exact action and report permission, revision conflict, duplicate request, and task creation failure as distinct outcomes.
  - Refresh from the canonical record after a conflict and preserve the user's draft for comparison and retry.
  - Acceptance: repeat submission or reload cannot create a second task, and both decisions retain their evidence lineage.

- [ ] **personal-04 — Connect Goals to human goal and plan records.**
  - Adapt `apps/assistant/src/features/personal/GoalsScreen.tsx` to show human goals separately from tasks and automation.
  - Provide goal title, purpose, status, target date, hierarchy, horizon, metrics, milestones, sessions, check-ins, and linked source evidence.
  - Use a dedicated detail view for the full plan; the compact Goals list shows status, next step, and latest observation.
  - Keep paused presentation, if offered, distinct from canonical archived and completed states.
  - Save a goal and its plan through their respective revisioned contracts; show a recoverable conflict with refreshed values.
  - Show linked task or run progress as evidence; completion of that work must not mark a human milestone complete.
  - Require an explicit human milestone or goal completion action and show when and by whom it was confirmed.
  - Validate units, dates, and parent selection before a write; report a rejected link without dropping the rest of the draft.
  - Acceptance: editing a goal, adding a check-in, and confirming a milestone survive reload with plan and goal revisions intact.

- [ ] **personal-05 — Give memory and persona distinct controls.**
  - Add `apps/assistant/src/features/personal/MemoryWorkspace.tsx` for what Gideon remembers about the user, with kind, source, scope, durability, and time.
  - Reuse native memory edit, forget, audit, and undo semantics; a forgotten item must no longer appear as an active fact.
  - Make source and scope visible before an edit or forget action, especially for inferred or imported facts.
  - Offer filters for active facts, episodes, preferences, and audit events without merging their meanings.
  - Add `PersonaSettings.tsx` for display name, tone, avatar, and user-facing preferences where supported.
  - Keep persona settings separate from the selected runtime agent and from permissions or capability settings.
  - Show clear read-only or unavailable states for memory controls the current account cannot use.
  - Announce save, forget, undo, and conflict outcomes in the focused panel without losing the selected item.
  - Acceptance: memory edits and persona changes appear in the right place after reload, with provenance and audit history available.

- [ ] **personal-06 — Open a complete Health workspace.**
  - Add `apps/assistant/src/features/personal/HealthWorkspace.web.tsx` using existing Health records and controls.
  - Preserve readings, labs, body, lifestyle, consumption, interventions, cognition, genome, imports, exports, sharing, and privacy routes.
  - Use dedicated detail screens and charts where a record cannot be understood from a compact card.
  - Separate user-entered data from connected-provider data and show connection readiness and last successful sync.
  - Show measurement units, observation time, source, revision, and freshness before comparisons or trend summaries.
  - Keep sensitive values out of broad Ideas, Activity, and search previews unless the user explicitly chooses to expose them.
  - Surface consent, access history, import errors, unavailable providers, and export scope in the relevant Health view.
  - Respect account changes and privacy settings in cached views, deep links, and return previews.
  - Acceptance: a user can inspect a dated record, its source and history, and navigate back without exposing unrelated private data.

- [ ] **personal-07 — Add Journal and relationship context.**
  - Add `apps/assistant/src/features/personal/JournalWorkspace.web.tsx` over the existing date-and-timezone journal record.
  - Keep one canonical entry per local date and timezone; show revisions and preserve a draft across navigation.
  - Let the user choose date and timezone explicitly, and show how an entry's day is interpreted before saving.
  - Keep manually written text distinct from suggested activity text until the user appends it.
  - Offer an activity-derived draft only as a preview with source citations and limitations; the user edits and saves it explicitly.
  - Link a personal reflection to a People or Compass detail by canonical ID when permission allows, without duplicating contact records.
  - Route contact edits, account identity, and relationship actions to the `communications` workspace.
  - When a related person cannot be viewed, retain the reflection and show a restricted-link state.
  - Acceptance: journal save/reopen preserves text and source links; a failed write retains the draft and offers a safe retry.

- [ ] **personal-08 — Qualify the connected personal journey.**
  - Add focused behavior coverage under `apps/assistant/src/features/personal/personal.test.tsx` and route coverage for the shared workspace.
  - Exercise Idea evidence and decisions, goal revision conflicts, explicit milestone completion, memory forget/undo, Health privacy, and journal retry through real typed contracts.
  - Cover cross-account cache clearing and the absence of automatic human-goal completion after a task succeeds.
  - Cover the accepted Idea's link to the exact task and a dismissed Idea's persistence after reload.
  - Check keyboard focus, labels, status announcements, reduced-width geometry, and return-to-chat behavior on desktop and phone web layouts.
  - Verify unavailable or permission-denied states without sample records or inert controls.
  - Record the exact canonical IDs and routes exercised by the behavior checks in the implementation evidence.
  - Acceptance: Chat to Idea to accepted task to Activity, and Apps to Goal, Health, Journal, and Memory each resolve to the canonical record.

## Integration result

The compact assistant surface shows useful personal summaries and direct actions. Detailed Health, Journal, and memory work has the space and privacy it requires. A task result can inform a human goal, while only the user confirms a personal milestone. Source links, decisions, revisions, and return context remain intact across reload and navigation.

Completion requires the actual records and controls to work together; a navigation tile without its destination is incomplete.

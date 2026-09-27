# Gideon Assistant Activity implementation list

Activity is the readable history of Gideon's work and the place to find work that needs attention. It summarizes canonical records and opens their proper detail views. It does not create a second task or run store.

The implementation paths under `apps/assistant/src/features/activity/` are proposed new paths. Existing console paths identify the records and detail experiences to reuse. Build after the `shell` and `delivery` foundations and the `conversation` baseline; connect to `work`, `communications`, `personal`, `library`, and `studio` destinations as those features arrive.

- [ ] **1. Define the typed Activity entry and source map.**
  - Add `apps/assistant/src/features/activity/types.ts` and `sourceMap.ts`.
  - Represent each entry with source kind, native source ID, event ID when present, occurrence time, status, actionability, title, summary, and typed destination.
  - Cover tasks, workflow runs, trigger runs, chat work, inbox items, approvals, notifications, and artifacts without assigning invented IDs.
  - Keep the source record and its latest event distinguishable; a new event may update a card without becoming a new task.
  - Preserve relationships among a task, its run, review, result, artifact, and originating conversation.
  - Keep task IDs separate from workflow run IDs even when one task launches the run.
  - Keep notification IDs separate from inbox item IDs even when they describe the same event.
  - Define stable keys from source kind and native ID for mixed lists and local selection.
  - Carry account and permission scope through the destination contract.
  - Define source-specific status mapping before rendering; retain blocked, skipped, cancelled, failed, waiting for input, and waiting for approval as distinct outcomes.
  - Mark missing timestamps or progress as unknown rather than substituting the current time or an estimated percentage.
  - Acceptance: the same native record keeps one stable identity through refresh and its displayed status never claims an unsupported state.

- [ ] **2. Compose a read-only Activity projection from existing Gideon records.**
  - Add `apps/assistant/src/features/activity/readActivity.ts` and `useActivity.ts`.
  - Read tasks, workflow runs, trigger history, inbox, approvals, notifications, and artifacts through named Gideon operations.
  - Reuse the existing task and workflow records exposed to `apps/console/src/features/tasks/` and `apps/console/src/features/workflows/`.
  - Reuse inbox and approval semantics represented by `apps/console/src/features/inbox/` and `apps/console/src/shared/data/attentionLanes.ts`.
  - Keep Activity a projection: no copied mutable task table, independent approval state, or fabricated worker health flag.
  - Page bounded source results and order by recorded occurrence time; a source without a timestamp appears with an explicit unknown date.
  - Carry per-source loading, freshness, permission, configuration, and error state so one failing source does not erase healthy results.
  - Read only sources available to the current account and clear cached results when that account changes.
  - Preserve source pagination cursors independently; a short task page must not hide later inbox items.
  - Avoid treating an API timeout as an empty task or approval list.
  - Label source coverage when a record family has no available read endpoint yet.
  - Recover with a fresh snapshot after event gaps, reconnects, account changes, and return from a detail route.
  - Acceptance: a read failure is visible and retryable while the last trustworthy snapshot is labelled stale, and an empty result is shown only after successful reads.

- [ ] **3. Build the Activity overview and attention lanes.**
  - Add `apps/assistant/src/features/activity/ActivityScreen.tsx`, `ActivityCard.tsx`, and `ActivityFilters.tsx`.
  - Place needs approval and needs input ahead of working, then recent finished work, with an all-items view and source filters.
  - Show each card's source, exact status, time, latest useful event, and linked result or artifact where available.
  - Keep approval records and their mirrored inbox notices to one actionable card while retaining the native links to both records.
  - Give notifications and passive receipts their own readable context; do not present them as tasks in progress.
  - Keep the source and status visible in both compact and expanded card layouts.
  - Announce counts for attention groups without relying on color alone.
  - Make filters reversible and expose the active choice to assistive technology.
  - Retain a clear link to all work even when the overview emphasizes recent items.
  - Use the shell's established spacing, cards, typography, themes, and five labelled destinations.
  - On narrow screens, keep actions reachable without horizontal scrolling; on wide screens, allow more detail without stretching the reading column.
  - Show meaningful first-use, filtered-empty, unavailable, and failed-read states with a retry or relevant destination.
  - Acceptance: users can tell what needs them, what is running, what finished, and which record produced each card.

- [ ] **4. Open typed details and preserve navigation context.**
  - Add `apps/assistant/src/features/activity/activityRoutes.ts` and `ActivityDetail.tsx`.
  - Deep-link by native task, workflow run, trigger run, chat session, inbox item, approval, notification, or artifact ID.
  - Open the existing task, workflow, inbox, and artifact experiences through the shared workspace frame where the full controls need room.
  - Keep Activity filters, scroll position, selected card, and originating conversation when moving to a detail and back.
  - Permit direct reload of a detail URL; fetch the canonical record instead of requiring a previously populated overview.
  - Point workflow runs to run detail and tasks to task detail even when their titles match.
  - Point artifacts to the actual artifact viewer and retain its source run or conversation link.
  - Open unsupported destinations with an honest availability state until their feature workspace ships.
  - Never form a route from an untrusted display title or an array position.
  - For missing, removed, forbidden, or differently owned records, show the actual reason and a safe route back to Activity.
  - Ensure keyboard activation, focus transfer, back behavior, and accessible labels identify the source and destination.
  - Acceptance: a result card opens the exact record, survives reload, and returns to the prior Activity context.

- [ ] **5. Connect attention cards to exact review and continuation actions.**
  - Add `apps/assistant/src/features/activity/ActivityAttention.tsx` and `activityActions.ts`.
  - Open the canonical approval or continuation detail before any decision; display the exact target, requested action, and available choices.
  - Route approve, reject, and answer through the same native review and conversation operations used by their detailed surfaces.
  - Preserve request identity and revision or decision constraints so a stale card cannot approve a different or superseded action.
  - Keep review text and decision controls together for keyboard and narrow-screen use.
  - Disable duplicate submission while a decision is pending without hiding the original request.
  - Show when another client has already resolved a request and reload its final state.
  - Leave domain-specific edit and send actions in their full detail views.
  - After an action, refresh the affected records and show the resulting state; keep a failed submission actionable with its error.
  - Distinguish stopping a conversation, cancelling a task, and rejecting an approval in labels and destinations.
  - Acceptance: an approval decision changes the canonical record once, and the corresponding Activity card reflects the result after refresh.

- [ ] **6. Add live updates with bounded snapshot recovery.**
  - Add `apps/assistant/src/features/activity/activityEvents.ts` and extend `useActivity.ts`.
  - Consume available conversation, workflow, and inbox events for timely card updates while preserving native source IDs.
  - Merge repeated or out-of-order events by source identity and recorded sequence or time; do not duplicate a card on reconnect.
  - Do not infer successful completion from a disconnected stream or a locally dismissed notice.
  - Mark individual sources stale when only their event channel stops.
  - Limit retained event history so a long-lived Activity tab remains responsive.
  - Coalesce frequent progress events while preserving the latest recorded stage.
  - Refresh source snapshots when the stream resumes, a sequence gap is detected, or a user returns from a full workspace.
  - Keep the last verified state visible with a stale indicator during disconnects and a clear retry path after failure.
  - Avoid whole-workspace polling that grows with the number of open conversations or cards.
  - Acceptance: a task progresses from Chat through review to a linked result without duplicate cards, including after reconnect and reload.

- [ ] **7. Qualify the Activity journey and handoffs.**
  - Add focused projection and interaction coverage under `apps/assistant/src/features/activity/` using real Gideon types and source semantics.
  - Exercise active, blocked, skipped, cancelled, failed, waiting, and completed cases, plus an approval mirrored in inbox.
  - Exercise one source failing while others load, empty versus unavailable states, stale recovery, direct-link reload, and inaccessible records.
  - Confirm cards from different accounts never mix after an account switch.
  - Confirm a repeated event does not duplicate a task or review card.
  - Confirm source filters and back navigation retain selection and scroll state.
  - Confirm no passive notification is offered as a task control.
  - Follow Chat to delegated work, progress, requested input or approval, result, artifact, and return to the same conversation.
  - Check keyboard and screen-reader labels for card actions and focus after detail navigation on desktop and narrow layouts.
  - Record actual observed behavior and remaining unsupported source journeys before declaring Activity complete.
  - Acceptance: the journey reads and acts on canonical records, and every visible status and link can be traced to its source.

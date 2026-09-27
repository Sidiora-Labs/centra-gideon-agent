# Gideon conversation implementation list

Purpose: make the assistant conversation the reliable center of the Gideon application. The chat screen keeps its compact assistant header, readable message layout, rounded composer, and clear result cards while using Gideon's authenticated sessions, execution, records, and actions.

The implementation starts in the open source application. The hosted edition receives the resulting interface with its existing account and policy adaptations. Conversation identity, transcript history, live execution, and queued work have one Gideon owner.

## Dependencies and shared contract

- `shell-01` defines destination IDs and frame/return contracts. The connected presentation in conversation-02 also requires the working web module host from `shell-07`.
- `delivery-01` supplies the authenticated same-origin bootstrap and session-safe request contract.
- Conversation owns its screen, controller, adapters, and chat-specific tests. It consumes the shell and delivery contracts without defining their authentication or navigation policy.
- `activity`, `work`, `communications`, `code`, `browser`, `library`, and `studio` may open a conversation or return a result through stable record and session IDs; their workspaces remain owned by those features.

## Sequential implementation tasks

- [ ] **conversation-01 — Establish one Gideon conversation controller.**
  - Add typed controller state and actions under `apps/assistant/src/shared/conversation/`, drawing from the existing session, turn, queue, and transport semantics in `apps/console/src/features/ChatPage.tsx`, `apps/console/src/features/chat/chatTypes.ts`, and `apps/console/src/shared/data/api.ts`.
  - Bind create/open/send and the live event stream to the authenticated account and active Gideon session. Only Gideon session keys identify conversations; screen-local IDs may identify visual elements but cannot own durable history.
  - Reduce live and recovered records by stable session, message, request, queue, tool, and approval IDs. A reconnect or navigation return must reconcile the authoritative snapshot with live events without duplicating a turn or resending an uncertain action.
  - Keep a draft on rejected submission, show an actionable connection/error state, and expose loading and empty states. An account switch clears inaccessible session data before loading the next account.
  - Proposed paths: `apps/assistant/src/shared/conversation/controller.ts`, `apps/assistant/src/shared/conversation/types.ts`, `apps/assistant/src/shared/conversation/controller.test.ts`.
  - Acceptance: a signed-in user opens a new session, sends once, and sees streamed text and final history under the same session key.
  - Acceptance: a late event for the previous session does not enter the current transcript after a switch.
  - Acceptance: recovery merges a persisted turn with its streamed version by identity and preserves the richer content.
  - Acceptance: a lost acknowledgement remains uncertain until the server snapshot resolves it; the client does not submit it again.
  - Acceptance: an expired session presents sign-in or session recovery before accepting another send.
  - Acceptance: account changes clear transcript, queued items, draft, and inaccessible result links from the prior account.

- [ ] **conversation-02 — Connect the real assistant chat presentation.**
  - Place the selected chat screen, message bubble, assistant response, composer, and status presentation in `apps/assistant/src/features/conversation/`; retain their component structure, spacing, cards, and accessible visual hierarchy under Gideon branding.
  - Replace presentation-owned execution and thread hooks with the typed controller from `conversation-01`. The screen renders one transcript and sends through one action path.
  - Use shell navigation and return context so opening a workspace and returning preserves the active session, draft, selected message, and scroll position. The compact chat width applies to chat content, not a launched full-width workspace.
  - Empty chat offers a clear first action. Loading, offline, failed send, and unavailable action states use visible text and a recovery action. Composer focus and message announcements work by keyboard and touch.
  - Proposed paths: `apps/assistant/src/features/conversation/ChatScreen.tsx`, `apps/assistant/src/features/conversation/AssistantResponse.tsx`, `apps/assistant/src/features/conversation/Composer.tsx`, `apps/assistant/src/features/conversation/chatStyles.ts`, `apps/assistant/src/features/conversation/ChatScreen.test.tsx`.
  - Acceptance: the first chat view shows the assistant identity and a visible, labelled way to start a conversation.
  - Acceptance: submitting a message displays a pending state, then the corresponding streamed assistant response.
  - Acceptance: returning from a result or workspace restores the same session and usable composer draft.
  - Acceptance: the send control explains why it is unavailable during authentication or connection recovery.
  - Acceptance: keyboard users can reach the transcript, message actions, composer, and navigation in a stable order.
  - Acceptance: a screen reader receives new message and error announcements without reading the entire transcript again.

- [ ] **conversation-03 — Render complete turns and truthful result cards.**
  - Adapt Gideon text, reasoning, tool lifecycle, approvals, activity, errors, citations, files, and structured results into typed presentation parts. Preserve source IDs, status, timestamps, and result links through streaming and history hydration.
  - Open a result at its actual destination and return to the originating session. Unknown producer types use a readable fallback card with available status and output; an absent result is never represented as success.
  - Keep rich content usable with long output, narrow screens, keyboard focus, and screen readers. A failed artifact or stale link offers retry or a clear error without discarding the conversation.
  - Proposed paths: `apps/assistant/src/shared/conversation/turnAdapter.ts`, `apps/assistant/src/features/conversation/ResultCard.tsx`, `apps/assistant/src/features/conversation/turnAdapter.test.ts`.
  - Acceptance: a running tool card changes to its observed terminal status after live and reloaded events.
  - Acceptance: an approval segment remains an approval, with its own ID and resolution, in a reloaded transcript.
  - Acceptance: citation and file controls open their real targets and retain their originating conversation on return.
  - Acceptance: missing preview metadata produces a concise fallback with the available title, status, and link.
  - Acceptance: long text and structured payloads wrap or scroll deliberately without clipping the composer.
  - Acceptance: an unavailable artifact gives a reason and leaves the rest of the turn readable.

- [ ] **conversation-04 — Complete history and branch continuity.**
  - Map the conversation switcher and history surface to Gideon session listing, create, rename, archive, restore, search, pin, folder, tag, and share actions where the account is permitted to use them.
  - Expose edit and resend, latest-answer regenerate and variant switch, fork at a turn, rewind, and branch lineage with labels that match the supported action. Keep parent and child sessions distinct and show which branch is open.
  - A stale or deleted session shows a recoverable history state. Switching during a live turn must not move incoming events into the newly opened transcript. Search and history controls support keyboard navigation and empty results.
  - Proposed paths: `apps/assistant/src/features/conversation/ConversationHistory.tsx`, `apps/assistant/src/features/conversation/MessageActions.tsx`, `apps/assistant/src/shared/conversation/history.ts`, `apps/assistant/src/shared/conversation/history.test.ts`.
  - Acceptance: a renamed or archived session updates the switcher from the server result, including after reload.
  - Acceptance: search distinguishes no matches from a failed history request and offers recovery for the latter.
  - Acceptance: pin, folder, tag, and permitted share actions reveal their resulting state in the session list.
  - Acceptance: editing a prior turn follows Gideon's rewind behavior and does not imply arbitrary historical mutation.
  - Acceptance: regenerate and variant controls appear only when the current answer supports them.
  - Acceptance: a fork opens a new session with a visible link back to its source and selected fork point.

- [ ] **conversation-05 — Preserve queue, steering, stop, and approval semantics.**
  - Show the server-backed queue with stable item IDs. Support queue after current response, steer the current response, remove/edit a queued item, and interrupt to promote queued work. Acknowledge each server result and reconcile after reconnect.
  - Label Stop as ending the active response and clearing queued work; show force stop only where the existing service allows it. A failed stop or interrupt leaves the actual server state visible rather than predicting success.
  - Approval cards show the exact requested action, input, risk, allowed decisions, revision field when supported, and resolved outcome. Preserve the approval ID and policy checks; one action cannot resolve a different request.
  - Empty queue, simultaneous events, expired approvals, and denied actions have explicit states. Buttons announce their effect and remain operable by keyboard and touch.
  - Proposed paths: `apps/assistant/src/features/conversation/QueuedMessages.tsx`, `apps/assistant/src/features/conversation/ApprovalCard.tsx`, `apps/assistant/src/shared/conversation/actions.ts`, `apps/assistant/src/shared/conversation/actions.test.ts`.
  - Acceptance: queue after current response creates a server item that survives screen navigation and reconnect.
  - Acceptance: steer is visibly distinct from queue after, and an acknowledged steer retains its entered text.
  - Acceptance: removing or editing a queued item updates only after the server confirms its ID.
  - Acceptance: interrupt preserves queued work while Stop explains that it clears queued work.
  - Acceptance: an expired approval cannot be submitted as a new decision; the resolved result remains visible.
  - Acceptance: revision and rejection send the exact approval request ID and preserve the action details for review.

- [ ] **conversation-06 — Restore composer capability and input recovery.**
  - Connect file upload, image and screen capture, paste, path, knowledge, and artifact context using typed metadata and existing validation. A failed upload stays attached as a retryable item; the composer never turns a record ID into plain prose as its only representation.
  - Connect speech input and playback to Gideon's voice services, with recording permission, busy, cancellation, transcription error, and playback states. Text entry remains available if voice is unavailable.
  - Put allowed agent, model, reasoning effort, task mode, and approval controls in understandable composer or header menus. Persist selection to the active session and display managed or unavailable choices accurately.
  - Preserve draft and attachment names/types while navigating; avoid retaining inaccessible file contents after sign-out or account switch. Keyboard submit, multiline entry, focus return, and mobile safe-area behavior remain usable.
  - Proposed paths: `apps/assistant/src/features/conversation/Composer.tsx`, `apps/assistant/src/features/conversation/ConversationControls.tsx`, `apps/assistant/src/shared/conversation/attachments.ts`, `apps/assistant/src/shared/conversation/voice.ts`, `apps/assistant/src/shared/conversation/composer.test.ts`.
  - Acceptance: an uploaded file keeps its validated reference, name, and type when sent and after reloading.
  - Acceptance: a failed upload can be removed or retried without deleting typed text.
  - Acceptance: screen capture and speech input show permission failure before sending partial content.
  - Acceptance: transcription can be reviewed and edited as text before submission.
  - Acceptance: a model or mode change displays its saved session value when the chat is reopened.
  - Acceptance: a managed choice explains its availability without suggesting an action the user cannot perform.

- [ ] **conversation-07 — Qualify the integrated conversation journey.**
  - Exercise authenticated new, send, stream, reload, switch, and return flows using real Gideon session and event contracts; verify the visible screen and canonical history agree after each transition.
  - Exercise a tool result and linked record, exact-action approval, queue versus steer versus interrupt versus stop, attachment and voice recovery, edit/regenerate/fork, and account separation. Record any unsupported control as unavailable with a reason until its real path exists.
  - Check desktop, tablet, and phone geometry; light and dark themes; keyboard, focus, screen-reader labels, long transcript behavior, and interrupted network recovery. Keep workspaces at their required width when opened from chat.
  - Use focused tests for each controller and presentation seam, then a browser journey through the shared application. Preserve session IDs and result links in the evidence so a reviewer can trace each observed state.
  - Proposed paths: `apps/assistant/e2e/conversation.spec.ts`, `apps/assistant/src/shared/conversation/controller.test.ts`, `apps/assistant/src/features/conversation/ChatScreen.test.tsx`.
  - Acceptance: the browser journey records real session and record IDs for send, result, approval, and branch transitions.
  - Acceptance: reloading while a response streams converges on one final visible answer and server history.
  - Acceptance: a dropped connection and a rejected action expose recovery without optimistic false success.
  - Acceptance: touch controls remain reachable above the phone keyboard and within tablet landscape width.
  - Acceptance: focus returns to the originating control after a sheet or workspace closes.
  - Acceptance: theme and responsive checks inspect the same real journey, including long output and active queue states.

Completion means the assistant screen performs these journeys through Gideon's real records and actions. Source presence, static screenshots, or passing adapter fixtures alone do not establish the integrated behavior.

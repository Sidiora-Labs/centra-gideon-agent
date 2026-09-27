# Gideon browser workspace

Give each conversation a persistent, owner-scoped browser that the user can inspect and control alongside Gideon. A browser session has a stable identity, a visible control owner, a recoverable preview, and an explicit lifecycle. Browser activity must remain subject to Gideon's existing permission and audit rules.

The workspace opens from a conversation or a relevant result, keeps the conversation link visible, and returns to the same conversation state. It uses the shared assistant frame from `shell`, the authenticated conversation identity from `conversation`, the customer workspace pattern from `code`, and the entry/auth contracts from `delivery`.

- [ ] **1. Establish the persistent browser session contract.**
  - Define one stable browser session ID linked to an owner, account scope, and conversation ID.
  - Persist the association before launching a browser so a lost launch response or reload cannot create an unrelated profile.
  - Define how a conversation with no browser receives its first session and how a closed one is shown.
  - Record creation time, update time, last site, lifecycle status, and the current engine connection.
  - Treat a requested session from an unrelated conversation as inaccessible even when its ID is known.
  - Make repeated create requests safe when the first response is delayed or lost.
  - Specify create, list, get, resume, close, and reconnect behavior with clear active, idle, closed, and error states.
  - Reject access from another owner or account at every metadata, preview, input, and download endpoint.
  - Keep the session record distinct from conversation messages, task runs, and desktop control sessions.
  - Reuse the existing browsing engine beneath the new customer session API.
  - Paths: `runtime/gideon/integrations/browse/customer_sessions.py` and `runtime/gideon/interfaces/dashboard/handlers/browser_sessions.py` (proposed), with route registration in `runtime/gideon/interfaces/dashboard/server.py`.
  - Acceptance: two conversations resume their own browser IDs after reload; cross-owner reads and writes fail without revealing session data.

- [ ] **2. Add a typed assistant browser client and route.**
  - Name operations for session list/get/create, navigation, preview, input, takeover, handback, downloads, close, and resume.
  - Use authenticated same-origin requests and the shared conversation ID; do not infer ownership from a URL parameter alone.
  - Distinguish capability unavailable, disconnected browser, expired session, denied permission, and retryable transport failure.
  - Deduplicate create and navigation submissions while a request is pending; reconcile uncertain results by reading the session.
  - Keep a typed client boundary between the workspace controls and backend response shapes.
  - Return to the initiating conversation even when the browser route was opened from a result card.
  - Show a retry action only where retry cannot repeat an uncertain browser write.
  - Leave controls disabled until their current session and permission state are known.
  - Keep raw browser credentials and engine endpoints out of browser-visible responses.
  - Paths: `apps/assistant/src/features/browser/browserClient.ts`, `apps/assistant/src/features/browser/browserTypes.ts`, and `apps/assistant/src/features/browser/BrowserRoute.web.tsx` (proposed).
  - Acceptance: opening the route from chat shows the correct session or an actionable state and never creates a duplicate on refresh.

- [ ] **3. Show a useful live preview and session context.**
  - Present current site, page title, last update, browser state, and who currently has control.
  - Render a bounded preview that refreshes from the server without exposing an engine socket or unrestricted page origin.
  - Provide an explicit open-in-workspace action, return-to-conversation control, and a visible loading state.
  - Preserve focus, draft, and scroll when moving between the conversation and browser workspace.
  - Offer a readable empty state when the conversation has never opened a browser.
  - Announce page changes and connection status without repeatedly stealing keyboard focus.
  - Keep the last good preview visibly dated while reconnecting.
  - Give previews a useful text description and an accessible route to page details.
  - Make the preview usable at desktop, tablet, and phone widths; give small screens deliberate full-screen controls.
  - Show stale preview and reconnect states when transport drops instead of implying the page is live.
  - Paths: `apps/assistant/src/features/browser/BrowserWorkspace.web.tsx` and `apps/assistant/src/features/browser/BrowserPreview.web.tsx` (proposed).
  - Acceptance: the user can identify the session and latest page, inspect it, navigate away, return, and recover after a disconnect.

- [ ] **4. Mediate navigation, input, and takeover.**
  - Give the user a clear URL field with validation and a visible navigation result.
  - Provide pointer, keyboard, scroll, and text input only while user control is active and the session is healthy.
  - Show a request-control action when Gideon has control, and require an explicit handback before assistant input resumes.
  - Define a single authoritative control holder and reject stale actions when ownership changes mid-request.
  - Label every control state in plain language: Gideon, you, paused, or unavailable.
  - Require a fresh state read after takeover or handback before enabling further input.
  - Handle control transfer during an in-flight navigation without reporting false success.
  - Expose the reason for rejected navigation or input near the action that failed.
  - Keep existing per-task browser grants, site scope, stop controls, and audit behavior for assistant actions.
  - Never silently switch an action to another browser or bypass an unavailable engine.
  - Paths: `runtime/gideon/integrations/browse/customer_control.py`, `runtime/gideon/interfaces/dashboard/handlers/browser_sessions.py`, and `apps/assistant/src/features/browser/BrowserControls.web.tsx` (proposed).
  - Acceptance: user takeover immediately blocks assistant input; handback resumes only permitted work, and an action refused by policy leaves the page unchanged.

- [ ] **5. Import downloads into owned files and results.**
  - List completed browser downloads with filename, size, state, and source session.
  - Import only selected completed downloads into the owner's file or artifact area.
  - Link imported results back to the conversation or work item using canonical file IDs.
  - Preserve existing file authorization, size checks, retention rules, and download scanning where available.
  - Keep an in-progress download separate from a completed file that can be imported.
  - Use a stable import request identity so a retry cannot create duplicate artifacts.
  - Show source filename and final Gideon file name when they differ.
  - Preserve a link from the file back to the browser session that produced it.
  - Show pending, unavailable, duplicate, and failed import states with safe retry behavior.
  - Paths: `runtime/gideon/interfaces/dashboard/handlers/browser_downloads.py` and `apps/assistant/src/features/browser/BrowserDownloads.web.tsx` (proposed).
  - Acceptance: a user imports a real downloaded file, opens it through Gideon's file route, and cannot import another owner's download.

- [ ] **6. Finish lifecycle and recovery controls.**
  - Make close, reopen, reconnect, and stop visibly distinct; closing a session must release its active engine resources.
  - Preserve the session record and conversation link long enough to explain its outcome and support intentional reopening.
  - Resolve a lost response by re-reading state before retrying a lifecycle mutation.
  - Make an already closed session safe to close again without reviving its engine.
  - Explain when reopening requires a new browser connection or fresh permission.
  - Show the last known page after a crash as history, not as an active preview.
  - Keep cleanup idempotent so retries cannot remove a newer active session.
  - Surface revoked grants, engine loss, and expired previews without presenting an editable stale image.
  - Keep a global stop effective across active assistant browser work and show the resulting state in the workspace.
  - Paths: `runtime/gideon/integrations/browse/customer_sessions.py`, `runtime/gideon/interfaces/dashboard/handlers/browser_sessions.py`, and `apps/assistant/src/features/browser/BrowserWorkspace.web.tsx` (proposed).
  - Acceptance: reload, process recovery, close, and reopen keep one session identity and display accurate control and resource state.

- [ ] **7. Qualify the complete browser journey.**
  - Exercise conversation launch, real navigation, preview, user takeover, user input, handback, assistant action, download import, close, and reopen.
  - Verify denied and expired grants, cross-owner isolation, lost connectivity, stale control tokens, and unavailable engines.
  - Confirm every visible action maps to an authorized backend mutation and a recoverable result.
  - Check keyboard focus order, control labels, preview alternative text, status announcements, and touch targets.
  - Record the session and conversation IDs behind the journey without exposing private page content.
  - Verify returning from a detail view preserves the selected conversation and draft.
  - Exercise a completed download and a rejected cross-owner download import.
  - Verify a stopped or denied assistant action does not change the page.
  - Inspect desktop, tablet, and phone layouts, including narrow URL entry and download overflow.
  - Paths: `checks/runtime/test_customer_browser_sessions.py` and `apps/assistant/src/features/browser/BrowserWorkspace.test.tsx` (proposed).
  - Acceptance: the end-to-end journey passes with actual browser state and source-linked evidence; partial engine availability is shown honestly.

This feature is complete when a signed-in user can return to the same conversation browser, inspect and safely take control of it, import its results, and close or reopen it without confusing operator browser attachment with their own session.

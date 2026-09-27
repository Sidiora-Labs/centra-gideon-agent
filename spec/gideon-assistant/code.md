# Code and Computer

Give Gideon users one place to open a project, inspect and edit its files, run a terminal, review live processes and ports, and return to the conversation that started the work. A customer Computer joins those tools around an owned workspace with a visible lifecycle and isolation state.

The Code destination is a full-width, full-height web workspace launched from Apps or a project, file, task, artifact, or chat result. The assistant header and return route stay available. On a narrow screen, use focused file, diff, status, and terminal views with explicit switching and readable controls.

Implementation depends on `shell` for the workspace frame and routing, `delivery` for authenticated entry, `conversation` for session and result links, and `discovery` for a labelled Code launch. `browser` may later link a preview to this workspace without owning its files or terminal.

Repository paths below are implementation targets. `apps/console/src/features/code/`, `files/`, `terminal/`, `artifacts/`, and `capabilities/workspace/` contain existing feature code. `apps/assistant/src/features/code/` is a proposed web module location; its entry should follow the shared shell contract.

- [ ] **1. Open the right project and workspace.**
  - Add a Code route that takes a project ID, optional workspace context ID, and optional file or result target.
  - Use the existing project list and saved workspace contexts to show the active path, branch, dirty state, linked terminals, and tasks.
  - Allow a project picker and recent project return without replacing the selected conversation.
  - Resolve launch parameters through typed route state so a copied link opens the same project for an authorized user.
  - Use the server's project and workspace IDs, rather than a display name, to select records.
  - Keep project switching explicit when a terminal or unsaved editor tab belongs to the prior workspace.
  - Show last known state while refreshing, then identify any branch or workspace change before the user acts.
  - Make unknown, deleted, inaccessible, and empty projects distinct states with a real route back to the picker.
  - Preserve selected project, pane, and scroll position when moving between Code and Chat or Activity.
  - Add the web host under `apps/assistant/src/features/code/CodeWorkspace.web.tsx`; adapt `apps/console/src/features/code/CodeCockpitPage.tsx` and `apps/console/src/features/capabilities/workspace/Projects.tsx` as trusted feature modules.
  - Acceptance: opening a linked project lands on that project's workspace; returning to the conversation restores its draft and context.

- [ ] **2. Browse and edit project files.**
  - Put a searchable file tree, tabs, path bar, preview, and editor in the wide workspace; keep the existing file service as the authority for reads and writes.
  - Carry file identity through rename, move, reload, and tab navigation; preserve an unsaved draft and warn before replacing it.
  - Scope search and path completion to the selected workspace and its allowed roots.
  - Show file type and size before opening a preview that cannot be edited.
  - Require a deliberate user action for delete and surface its result at the affected path.
  - Keep tab labels distinct when two open files share a name.
  - Show permission failures, missing files, binary preview limits, stale versions, and write conflicts with retry or refresh actions.
  - Keep keyboard file search, clear focus order, editor labels, and a visible save state.
  - On phones, show a focused reader or editor and a deliberate switch back to the file list.
  - Adapt `apps/console/src/features/files/FilesSection.tsx`, `browse/FileTree.tsx`, `browse/FileViewer.tsx`, and `browse/useFileTabs.ts`; add a Code file pane under `apps/assistant/src/features/code/`.
  - Acceptance: a user opens, edits, saves, reloads, and reopens a real project file, including recovery after a conflicting update.

- [ ] **3. Bring artifacts and diffs back to their source.**
  - Show file changes and artifact results with the originating project, task, run, and conversation when those links exist.
  - Reuse artifact version and raw-content operations for preview, comparison, save, and download; preserve file-backed live pointers.
  - Show whether a result is a live file or a saved artifact version before an edit or download.
  - Preserve the selected comparison pair when moving between diff and file editor.
  - Associate a result with the exact source ID when multiple tasks wrote the same path.
  - Keep a useful text or metadata fallback when rich preview cannot load.
  - Keep generated or unsafe previews within the existing isolated preview boundary.
  - Make a missing artifact, unavailable preview, stale version, and failed download understandable and recoverable.
  - Let a file or artifact card open its exact Code target and return to the source result.
  - Adapt `apps/console/src/features/code/DiffView.tsx`, `apps/console/src/features/artifacts/ArtifactViewer.tsx`, and `apps/console/src/features/artifacts/ArtifactCompare.tsx`.
  - Acceptance: selecting a changed file or result opens the correct version and a working route back to its source.

- [ ] **4. Attach real PTY terminal sessions.**
  - Reuse the terminal session create/list/delete API and PTY WebSocket; expose the chosen working directory and actual sandbox state.
  - Bind terminal tabs to the selected workspace and show alive, connecting, disconnected, stopped, and capacity-limited states.
  - Show whether the selected provider or sandbox can actually be used before offering Start.
  - Refuse terminal input until the authorized PTY connection is established.
  - Identify an intentional session close separately from a dropped socket.
  - Avoid exposing an old terminal's output when the user changes project or account.
  - Offer reconnect to a surviving session and explicit close; a connection loss must not imply that the shell stopped.
  - Preserve keyboard input, terminal resize, copy behavior, focus escape, and screen-reader labels.
  - Use `apps/console/src/features/terminal/TerminalView.tsx`, `TerminalPage.tsx`, `terminalBridge.ts`, and `SandboxPicker.tsx` through a proposed Code terminal pane.
  - Acceptance: create, reconnect to, and close a real PTY from the selected workspace; state after reload matches the server session.

- [ ] **5. Show processes, ports, and Git for the selected project.**
  - Reuse workspace process, log-window, port, project, and Git services with their native IDs and revision checks.
  - Keep process and port records attached to their actual workspace, not merely the current visual selection.
  - Show the source of a listening port and whether its exposure is local or shareable.
  - Preserve the selected file when moving from Git status to a diff and back.
  - Refresh a conflicting revision before offering a retry that could affect a different process.
  - Show process command, status, logs, stop action, and explicit failure; show owned port, listener, exposure, and conflict state.
  - Show branch, dirty files, file diffs, and recent commits without implying that a view made a commit.
  - Require confirmation for process stop or other destructive action, and show access denial or unavailable services honestly.
  - Keep log updates bounded and allow users to resume at the last cursor after a disconnect.
  - Adapt `apps/console/src/features/capabilities/workspace/Processes.tsx`, `Ports.tsx`, and `Git.tsx` into Code panels.
  - Acceptance: project status matches the existing services, and a failed stop or port action remains visibly failed.

- [ ] **6. Define the customer Computer lifecycle and isolation contract.**
  - Bind a customer-owned Computer record to an authorized project, workspace path, terminal sessions, files, processes, and ports.
  - State who owns the Computer, which project it serves, and what persists after Stop.
  - Treat starting twice, stopping twice, and reconnecting after reload as idempotent user journeys.
  - Confirm the actual sandbox in the lifecycle response before displaying Ready or Isolated.
  - Keep a failed start available for retry without silently switching to a weaker execution mode.
  - Show unconfigured, starting, ready, reconnecting, stopped, unavailable, and failed states with explicit start, reconnect, and stop actions.
  - Decide the required isolation tier for each product mode. An isolated request must fail visibly if the selected sandbox cannot start; it must never report an isolated Computer while running a host shell.
  - Keep workspace files according to the stated retention policy when a Computer stops; release live processes and sessions by an explicit lifecycle rule.
  - Prevent a project switch or second account from inheriting another owner's terminal or path.
  - Keep customer Computer separate from operator desktop controls and their permissions.
  - Add the owned lifecycle in `runtime/gideon/interfaces/dashboard/handlers/` and `runtime/gideon/workspace/capabilities/workspace/`; adapt the terminal handler's sandbox failure path.
  - Acceptance: an authorized user starts, reconnects to, and stops a Computer; an unavailable required sandbox leaves no silently active host shell.

- [ ] **7. Join the journey and qualify responsive behavior.**
  - Open Code from Apps, Chat, Activity, project details, and artifact results using stable deep links.
  - Keep the selected project and return target through refresh, reconnect, and navigation; do not duplicate terminal sessions on remount.
  - Verify direct links to deleted or unauthorized source records show recovery rather than an empty editor.
  - Verify account switching clears workspace tabs, terminal output, and result previews from the prior owner.
  - Verify keyboard navigation can reach pane switching, save, close, and return actions.
  - Verify a stopped Computer remains visibly stopped while its retained files can still be inspected when policy allows.
  - On desktop and tablet, let editor, terminal, diff, and status panes use the available width and resize without clipping.
  - On phones, prioritize readable status, file review, and focused terminal access with clear touch targets; preserve keyboard access on all sizes.
  - Show a precise unavailable state for any missing provider or capability, with the permitted next action.
  - Add focused route, file, PTY, lifecycle, and responsive journey checks beside the relevant modules.
  - Acceptance: a user goes from a chat result to its project file, reviews its diff, opens its PTY, sees a process and port, then returns to the same conversation on desktop, tablet, and phone.

# Gideon Assistant Work implementation list

Purpose: make Gideon's existing work records and controls usable from the assistant application. Activity summarizes current work; dedicated, responsive workspaces let people plan, run, review, and recover tasks, projects, workflows, triggers, loops, rooms, agents, skills, tools, and experiments. Every state and action resolves to its canonical Gideon record.

Dependencies: `shell` provides labelled routes and the wide workspace frame; `delivery` provides authenticated same-origin access; `conversation` provides durable session links; `activity` supplies typed work entries; `discovery` supplies labelled launches. The shared destination checklist records coverage; Work owns detailed work controls and return links. A result opened from Chat or Activity retains its originating session and record ID.

- [ ] **01 — Establish Work routes and typed record contracts.**
  - Add a Work index and dedicated routes for tasks, projects, workflows, runs, triggers, loops, rooms, agents, skills, tools, and experiments.
  - Use the shared assistant header, workspace frame, theme, focus rules, and full-width layout for dense views.
  - Keep source type, canonical ID, revision, permissions, readiness, and originating conversation in route state; restore them after reload and back navigation.
  - Provide named read and write operations for each record family through the authenticated client; do not put arbitrary request paths in screen controls.
  - Show distinct empty, unavailable, stale, and failed-read states with useful retry or connection actions.
  - Preserve current deep links while linking Work, Activity, Apps, and Chat to the same details.
  - Keep Skills and Tools catalogue, detail, edit, execution, and availability destinations reachable through labelled Work routes and Apps launches.
  - Use labelled search and filters so a user can locate a record without remembering its ID.
  - Show the owning project, room, agent, or workflow when a record has one; omit unsupported relationships.
  - Restore focus to the launching control when a detail closes and announce navigation changes.
  - Acceptance: opening a source-linked record after reload reaches the same authorized record and return point.
  - Intended paths: `apps/assistant/src/features/work/WorkRoutes.web.tsx`, `apps/assistant/src/features/work/workClient.ts` (proposed); existing route source: `apps/console/src/app/shell/navigationModel.ts`.

- [ ] **02 — Bring tasks and projects into functional workspaces.**
  - Show a task list, board, detail, dependency graph, plan, assignee, attempts, comments, evidence, and linked runs without flattening statuses.
  - Create and edit tasks through their existing validation rules; support explicit block, resume, cancel, and completion actions where the record permits them.
  - Show project membership, linked work, ownership, context, and project actions with a direct path to each task.
  - Keep project task planning and work evidence in Work; launch project code, files, terminal, and execution views in Code from the same project ID and store.
  - Keep task and project IDs visible in navigation and retain filters, selection, and unsaved input when users return from Chat or Activity.
  - Explain permission failures and edit conflicts while preserving the user's draft; show retry only where the action is safe to repeat.
  - On narrow screens, offer readable task review and focused editing instead of compressing a desktop board or graph.
  - Include a clear ready or blocked reason from dependency and validation state before allowing a start action.
  - Separate task completion evidence from a workflow run's terminal status and from a human goal's progress.
  - Display archived or deleted source links as unavailable records with their original source type.
  - Acceptance: an authorized edit persists once; a rejected edit retains its draft and exact target.
  - Intended paths: `apps/assistant/src/features/work/TaskWorkspace.web.tsx`, `apps/assistant/src/features/work/ProjectWorkspace.web.tsx` (proposed); existing source: `apps/console/src/features/tasks/TaskDetail.tsx`, `apps/console/src/features/projects/ProjectHub.tsx`.

- [ ] **03 — Make workflow definitions and DAG editing usable in Work.**
  - Open a workflow list, definition detail, and full-width graph canvas with node selection, ports, dependencies, validation, and version context.
  - Expose the actual save, edit, publish, and start controls available to the current user; state preflight problems before starting.
  - Keep graph selection and viewport when opening a node inspector or returning from a run.
  - Give keyboard users a reachable node list and inspector path, with labelled ports and validation errors.
  - Preserve an unsaved definition after a failed save and show a revision conflict with recovery choices.
  - Let users inspect a node's inputs, outputs, rules, and upstream/downstream connections before publishing.
  - Surface missing credentials or provider readiness as a specific preflight result when the backend reports it.
  - Keep an existing published version inspectable while an editable revision is in progress.
  - Acceptance: a saved graph reloads with the same nodes and edges, and invalid changes remain clearly unsaved.
  - Intended paths: `apps/assistant/src/features/work/WorkflowEditor.web.tsx`, `apps/assistant/src/features/work/WorkflowNodeInspector.web.tsx` (proposed); existing source: `apps/console/src/features/workflows/WorkflowDefDetail.tsx`, `apps/console/src/features/workflows/NodeInspectorDrawer.tsx`.

- [ ] **04 — Connect workflow runs, reviews, and evidence to their definitions.**
  - Show run status, node timeline, logs, approvals, input requests, deliverables, artifacts, and source task or project links.
  - Bind pause, steer, confirm, retry, resume, and cancel controls only to supported run states and exact pending action IDs.
  - Show the result of a review action and refresh the run from its durable source after a disconnect; never infer success from a click.
  - Make every run and artifact deep link return to its definition, source conversation, and Activity entry.
  - Distinguish a completed run from a completed task and distinguish waiting for input from active execution.
  - Provide a readable mobile run timeline and accessible action labels, error text, and focus return after review.
  - Keep run event ordering and timestamps visible so a reconnect cannot silently reorder evidence.
  - Present the exact decision target and effect before a user confirms an approval or continuation.
  - Mark action buttons busy while a write is pending and permit retry only after checking current source state.
  - Acceptance: a completed action survives reload and links to the resulting event or artifact.
  - Intended paths: `apps/assistant/src/features/work/WorkflowRunWorkspace.web.tsx`, `apps/assistant/src/features/work/workflowRunState.ts` (proposed); existing source: `apps/console/src/features/workflows/WorkflowRunDetail.tsx`, `apps/console/src/features/workflows/ReviewTriagePanel.tsx`.

- [ ] **05 — Make triggers and loops controllable workspaces.**
  - Show trigger type, schedule or event source, target action, enabled state, readiness, next occurrence, and run history.
  - Support create, edit, test, enable, disable, and run actions only where each trigger type and permission allows them.
  - Present loop intake, preflight, plan review, execution progress, questions, controls, and final report as one traceable journey.
  - Preserve the distinction between trigger execution, loop execution, task progress, and chat messages in links and status text.
  - Show missed or failed runs with evidence and repair actions; keep drafts after validation or network failures.
  - Make calendars, histories, and loop controls keyboard navigable and usable at phone width.
  - Show local time and time zone alongside the next scheduled trigger occurrence.
  - Explain unsupported trigger kinds or unavailable event sources at creation time.
  - Preserve the selected plan step and question after a loop stream reconnects.
  - Acceptance: a trigger or loop control updates the durable state and its next available action.
  - Intended paths: `apps/assistant/src/features/work/TriggerWorkspace.web.tsx`, `apps/assistant/src/features/work/LoopWorkspace.web.tsx` (proposed); existing source: `apps/console/src/features/triggers/TriggersSection.tsx`, `apps/console/src/features/loops/LoopCockpitPage.tsx`.

- [ ] **06 — Operate rooms, agents, skills, and tools.**
  - Show a room index and conversation with members, roles, presence, shared instructions, files, assignments, approvals, and linked work.
  - Keep room IDs and context separate from side conversations; entering and leaving a room retains the primary assistant conversation.
  - Show agent definitions, provider readiness, ownership, routing, permissions, assigned work, and recent execution evidence.
  - Show installed and available Skills and Tools catalogues with searchable names, ownership, readiness, and source details.
  - Open skill detail and files; create or edit a skill, verify integrity, and show install or removal outcomes where permitted.
  - Open tool detail and input schema; run a permitted tool with exact risk review, then show its real result or error.
  - Show disabled providers, unavailable servers, locked platform tools, and approval requirements before offering a control.
  - Make create, edit, membership, assignment, and agent selection actions reflect canonical results and access controls.
  - Show unavailable members, failed sends, invalid configuration, and permission denial with actionable recovery and no false completed state.
  - Keep roster, message input, and pending approval accessible by keyboard and on tablet and phone layouts.
  - Show which agent or person performed each room action and which record received an assignment.
  - Require exact selection for destructive membership or agent changes; display the resulting roster/configuration.
  - Preserve unsent room text through a failed send and explain unavailable recipient or channel state.
  - Acceptance: room context and the primary assistant thread remain distinct after refresh and navigation.
  - Intended paths: `apps/assistant/src/features/work/RoomWorkspace.web.tsx`, `apps/assistant/src/features/work/AgentWorkspace.web.tsx`, `apps/assistant/src/features/work/SkillsWorkspace.web.tsx`, `apps/assistant/src/features/work/ToolsWorkspace.web.tsx` (proposed); existing source: `apps/console/src/features/rooms/RoomsSection.tsx`, `apps/console/src/features/agents/AgentDetail.tsx`, `apps/console/src/features/skills/SkillsPage.tsx`, `apps/console/src/features/tools/ToolsPage.tsx`.

- [ ] **07 — Expose experiments and replay evidence.**
  - List experiments with owner, purpose, configuration, status, selected run, and source workflow or agent.
  - Let users start supported experiments, inspect runs, compare outputs and score breakdowns, and replay with explicit input and version context.
  - Link each observation to the durable run, artifact, and source record so results can be opened from Activity or Chat.
  - Label incomplete evidence, unavailable providers, failed replay, and permission limits without manufacturing a success score.
  - Preserve filters and the selected comparison across route changes; provide a text table equivalent for visual comparisons.
  - Show a replay's inputs, evaluation version, and selected baseline beside the compared result.
  - Present score definitions and missing measurements so a chart is interpretable.
  - Make replay start and cancellation state explicit when those actions are supported.
  - Acceptance: two selected runs produce a traceable comparison with links to their raw evidence.
  - Intended path: `apps/assistant/src/features/work/ExperimentWorkspace.web.tsx` (proposed); existing source: `apps/console/src/features/experiments/ExperimentsPage.tsx`.

- [ ] **08 — Complete the cross-workspace work journey.**
  - Connect Chat delegation to a canonical task, project or run, then to Activity progress and the relevant Work detail.
  - Resolve pending review or input at its exact source, then show the durable result and artifact with a route back to Chat.
  - Check that workspace route, selection, draft, and scroll restoration survive reload, back navigation, and disconnection.
  - Check desktop, tablet, and phone geometry for every Work family, including graph, room, timeline, and comparison views.
  - Verify keyboard navigation, focus return, meaningful labels, and readable status changes across the journey.
  - Record unresolved capabilities as explicit availability states and retain current deep links until their new destinations work.
  - Include a denied-action path and a provider-unavailable path in the journey evidence.
  - Confirm a completed workflow run does not silently complete a separate project task.
  - Confirm a room-linked task opens the same task detail as a Chat or Activity link.
  - Confirm a Skill or Tool opened from Apps reaches its full Work detail and returns to its source.
  - Acceptance: each family has a real read path, a permitted control path, and an evidence return path.
  - Intended paths: `apps/assistant/src/features/work/WorkRoutes.web.tsx`, `apps/assistant/e2e/work.spec.ts` (proposed).

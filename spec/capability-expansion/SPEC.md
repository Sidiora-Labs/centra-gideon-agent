# Gideon capability expansion specification

Status: in progress. Version: 1.0. Date: 2026-09-25.

## Objective

Deliver the complete capability inventory below as coherent Gideon features, preserving its existing agent runtime and product identity. The machine-readable backlog is `backlog.json`; per-task evidence lives in `tasks/<id>.json`. A capability remains pending until its concrete acceptance is met.

## Acceptance applying to every task

1. Each capability is a complete vertical slice with persistent records or a projection of existing records, typed operations, HTTP and agent access where meaningful, discoverable console interaction, and durable error/retry semantics.
2. Reuse existing knowledge, artifacts, workflows, providers, auth, notifications and durability; do not create competing stores for existing authoritative records. New domain stores must be enrolled in durability.
3. No fake, stub or placeholder success, simulated media, invented measurements, count-only qualification or tests that replace the behavior being qualified. Filesystem fixtures and real local HTTP applications are allowed; fabricated service/provider doubles are not.
4. Every accepted behavior has an independently asserted real-path test. New or changed executable test lines must be at least new or changed executable production lines (1:1). Exclude comments, blank lines, generated assets and fixtures from both totals; no padding or duplicate assertions to meet the ratio.
5. One task per agent. Implement full declared task before one compile check and one focused verification command; at most two scoped repair/rerun cycles and 30 minutes per dispatched task. Root reassigns continuation after the cap; no false done state.
6. Source implementation, local qualification, external/native qualification and integrated qualification are distinct statuses. External credentials, GPU/native devices and deployment approval may block specific journeys without blocking independent backlog tasks.
7. Record exact changed paths, schema/version, acceptance coverage, command, exit code, log, source revision and remaining limits. CI/workflow tooling enforces task state, ownership, dependency readiness and test ratio.
8. Use existing design tokens and shared controls, clear loading/empty/error states, keyboard access, mobile geometry and URL-addressable record selection. Preserve existing sessions and navigation continuity.
9. New state has explicit source identity, timezone semantics where applicable, bounded validation, optimistic conflicts, atomic writes/transactions and idempotency for retryable mutations. Distinguish absent/unknown from empty/zero.
10. Provider integrations use published app/SDK contracts. Expensive or native work uses existing sidecars/supervision, with exact availability and cancellation states. Preserve the execution and permission boundary.

## Shared integration ownership

The root integrator owns dashboard server registration, console shell/navigation, native tool metadata, shared artifact/provider contracts, package manifests and durability inventory. Lane agents own the paths declared by their task. Agents submit exact integration hooks rather than concurrently editing shared files. New handlers expose `register(app)` and new lane pages export a default React component from `Page.tsx`; concrete subroutes belong to that lane. HTTP prefix `/api/capabilities/<lane>` and console route `#/capabilities/<lane>` are reserved. Existing-domain adapters may live in the existing domain when the task declares exact paths. No generic document database may replace the existing knowledge or artifact store.

## Publication

Every completed qualified task is committed with only its declared changes and pushed to its upstream task branch. Root integrates completed tasks into main and pushes without force. Do not stage unrelated files, dependencies or credentials. This repository contains only the open-source product; specifications, comments, metadata, fixtures and commits describe Gideon directly. Preserve legally required dependency notices when applicable.

## Initial dispatch contracts

### workspace.01 — Workspace context snapshots and reconciliation

Contract: Snapshot: id, project_id, workspace, branch, dirty, terminal_ids, task_ids, captured_at, revision. Reconciliation reports surviving and missing references and branch mismatch; never changes branches or recreates processes.

Acceptance: Capture real temporary Git repository context, reload durable state, change branch and terminate one referenced session, reconcile accurately. Reject escaping or inaccessible workspace paths. UI save/list/compare/delete uses real HTTP and retains errors.

### knowledge.01 — Anniversary resurfacing

Contract: Read projection: user timezone and date; prior-year journal/note/memory source IDs, original timestamps, title, excerpt, source link. No second source datastore. Current-year entries excluded; explicit leap-day policy and bounded pagination.

Acceptance: Create real records in current knowledge store; query anniversaries by original source date and timezone, exclude this year, test leap day and pagination; view/open exact source via console; no provider calls.

### identity.01 — Autobiography stories and follow-up chains

Contract: Story: id, prompt, theme, text, parent_id, created_at, updated_at, revision; original answers preserved independently from generated narratives. Parent references local and acyclic. Optimistic edits and delete policy preserve coherent chains.

Acceptance: Author parent and child through HTTP, edit with conflict handling, reload persisted chain, refuse cycles/cross-store references, export complete chronology, render editable chain through console. Automatic interview generation is subsequent work unless actual provider integration is supplied.

### wellbeing.01 — Body measurements and blood pressure records

Contract: Measurement: id, kind(body_weight,blood_pressure), observed_at with offset, normalized unit, values, source, notes, created_at, revision. Validate finite positive ranges and typed pressure components; immutable provenance, correction history.

Acceptance: Enter and correct real measurements via HTTP/UI, retain history after store reopen, filter dates accurately, reject malformed units/nonfinite values/stale writes; two isolated homes cannot share data; export represents exact records.

### communications.01 — People and relationship touchpoints

Contract: Person: id,name,identities,ring,cadence_days,notes,revision. Touchpoint: id,person_id,source,external_id,occurred_at,direction,summary. Identity normalization never silently merges ambiguous people; source replay idempotent.

Acceptance: Create person, add/edit identities, record and replay a touchpoint without duplication, calculate overdue state in configured timezone, preserve notes after reload, reject invalid references and stale writes; console care/detail flow works.

### media.01 — Editable sketches and original-preserving image overlays

Contract: Sketch: id, source_artifact_id and version or blank canvas, width,height,strokes,revision,updated_at; validated bounded coordinates/colors/brush widths. Retain original artifact, editable sidecar and explicit derivative relationship.

Acceptance: Use real image bytes and actual artifact storage; draw/erase/undo in console, save/reopen strokes, export flattened PNG with correct dimensions, verify source unchanged, reject stale updates and malformed/oversized input.

### creative.01 — Creative ingredient catalog and revisions

Contract: Ingredient: id,type(character,place,object,theme,event,concept),title,body,tags,source_refs,relations,created_at,revision. Stable IDs, immutable revisions, restorable values and explicit missing-reference behavior.

Acceptance: Create/edit ingredient through actual HTTP/UI, attach source references, relate two records, restore previous revision without losing history, reopen durable store, filter/search/paginate, reject dangling/cyclic-invalid relations and stale writes.

### music.01 — Repertoire and spaced practice

Contract: Repertoire item: id,title,instrument,body,attachment_refs,stage,practice_history,due_at,ease,interval,repetitions,revision. Grade 0..5; deterministic versioned spaced-practice rules and idempotent attempt IDs.

Acceptance: Create actual repertoire document, submit real graded practice through HTTP, retry same ID once, show same persisted schedule/history after reopen, handle timezone and failure grade, inspect reader/practice UI and valid existing attachments.

### experience.01 — Interactive story graphs and durable player state

Contract: Story: id,title,nodes,transitions,start_node,revision. Node has text,kind(scene,ending),choices with stable IDs and target IDs. Session: id,story_id,story_revision,current_node,history,revision. Deterministic choice transitions tied to source revision and idempotent request IDs.

Acceptance: Create story with two endings via UI/API, start and choose alternate branches in separate sessions, reload/resume exact state, reject dangling nodes/unreachable invalid graphs/stale edits/duplicate request conflicts; no fabricated model responses.

### platform.01 — HTTP and event API explorer

Contract: Authenticated read-only API catalog derived from actual aiohttp route registration; method,path,name,handler metadata and explicitly declared event schemas. Unknown schemas labeled unknown; no execution introspection that leaks secrets or filesystem paths.

Acceptance: Build actual aiohttp application with real production routes, retrieve catalog through authenticated boundary, verify method/template match including dynamic routes and removal, bounded payload and secret omission; console filters and harmless read execution display actual responses/errors.

## Complete capability backlog

All listed capabilities remain in scope. The first task in each lane is immediately eligible; further tasks refine their exact scope and true cross-lane prerequisites before starting. Sequential defaults prevent unreviewed parallel mutation within a domain, not a reason to leave another independent lane idle.

### workspace

- `workspace.01` Workspace context snapshots and reconciliation
- `workspace.02` Managed external application processes
- `workspace.03` Host port reservations and conflict inventory
- `workspace.04` Existing project discovery and scaffolding
- `workspace.05` Operator Git and submodule operations
- `workspace.06` Human remote desktop sessions
- `workspace.07` External terminal mirroring
- `workspace.08` Provider terminal launch and image attachments
- `workspace.09` Managed process logs
- `workspace.10` Attributed storage diagnosis

### knowledge

- `knowledge.01` Anniversary resurfacing
- `knowledge.02` Capture classification and correction
- `knowledge.03` Personal record type mappings
- `knowledge.04` Conversation archive import
- `knowledge.05` Tracked cross-domain topics
- `knowledge.06` Obligation digests and weekly review
- `knowledge.07` Date-keyed journals with activity drafts
- `knowledge.08` Idea-list vault exchange
- `knowledge.09` Video transcript and source ingestion
- `knowledge.10` Link collections and repository study
- `knowledge.11` External vault registration and editing
- `knowledge.12` RSVP reader

### identity

- `identity.01` Autobiography stories and follow-up chains
- `identity.02` Human digital twin and enrichment
- `identity.03` Twin fidelity evaluations
- `identity.04` Human life goals and calendar sessions
- `identity.05` Human progress sheet
- `identity.06` Protected agent continuity and lifecycle
- `identity.07` Encrypted selective identity bundles
- `identity.08` Versioned bounded tool recipes

### wellbeing

- `wellbeing.01` Body measurements and blood pressure records
- `wellbeing.02` Blood results and clinical record history
- `wellbeing.03` Apple Health XML ZIP JSON and FHIR ingestion
- `wellbeing.04` Alcohol and nicotine tracking
- `wellbeing.05` Genome source index and annotations
- `wellbeing.06` Intervention records and adherence
- `wellbeing.07` Timed cognitive exercises and scoring
- `wellbeing.08` Spaced memory and cognitive practice
- `wellbeing.09` Life calendar and completion reminders
- `wellbeing.10` Versioned health export
- `wellbeing.11` Native health shared-store integration
- `wellbeing.12` Privacy subjects consent and encrypted facts
- `wellbeing.13` Organization holdings and change propagation
- `wellbeing.14` Broker case lifecycle and verification

### communications

- `communications.01` People and relationship touchpoints
- `communications.02` Contact import and identity resolution
- `communications.03` Care cadence and unanswered-thread evidence
- `communications.04` Account-bound email and message mirrors
- `communications.05` iMessage and Signal Desktop imports
- `communications.06` Beeper conversations attachments and durable outbox
- `communications.07` Telegram operational commands and notifications
- `communications.08` External calendar mirroring and daily review
- `communications.09` Social account registry
- `communications.10` X reading and reviewed compose handoff
- `communications.11` Stacker News territory and action workflow
- `communications.12` Agent platform account lifecycle
- `communications.13` Human activity timeline

### media

- `media.01` Editable sketches and original-preserving image overlays
- `media.02` Media library facets and collection membership
- `media.03` Media annotations and attribution
- `media.04` Durable media job lifecycle
- `media.05` Image and video readiness contracts
- `media.06` Advanced image controls and conditioning
- `media.07` LoRA discovery and compatibility
- `media.08` LoRA datasets and checkpoint training
- `media.09` Image cleanup transforms
- `media.10` Advanced video generation and continuation
- `media.11` Video timeline and FFmpeg rendering
- `media.12` Continuous scene episodes
- `media.13` Sprite generation and atlas compilation
- `media.14` Code animation workspace
- `media.15` Media source downloader

### creative

- `creative.01` Creative ingredient catalog and revisions
- `creative.02` Moodboards and source references
- `creative.03` Universe canon and visual identity
- `creative.04` Universe relationship graph and merge
- `creative.05` Literary authors and voice configuration
- `creative.06` Writing works exercises and versioned drafts
- `creative.07` Bounded manuscript polishing and promotion
- `creative.08` Guided story development
- `creative.09` Series volumes arcs and staged drafting
- `creative.10` Reverse outline and continuity ledger
- `creative.11` Voice fingerprint diagnostics
- `creative.12` Editorial checks and anchored reversible repairs
- `creative.13` Bounded series production workflow
- `creative.14` EPUB and print manuscript export
- `creative.15` Creative direction and production plans
- `creative.16` Recurring commissions and feedback

### music

- `music.01` Repertoire and spaced practice
- `music.02` Artists albums tracks and selected renders
- `music.03` Music generation and engine capabilities
- `music.04` Musical canons and multi-part practice
- `music.05` Audio transcription to MIDI and piano roll
- `music.06` Beat-aware music video composition
- `music.07` Image-to-3D assets and export
- `music.08` Procedural model schemas refinement and diagnostics
- `music.09` Listening history and playlist ingestion
- `music.10` Playing-card and tarot design

### experience

- `experience.01` Interactive story graphs and durable player state
- `experience.02` Hosted story participation and voice
- `experience.03` Spoken interface navigation and action receipts
- `experience.04` Proactive speech output ownership
- `experience.05` Fullscreen ambient display
- `experience.06` Animated avatar driven by actual state
- `experience.07` Native audio call adapter and session handoff
- `experience.08` World engine application integration
- `experience.09` World projection presence and editing
- `experience.10` Scoped world guests and travel
- `experience.11` World foundations and controller lifecycle
- `experience.12` Game asset binding and compilation
- `experience.13` Moltworld adapter
- `experience.14` Moltbook adapter

### platform

- `platform.01` HTTP and event API explorer
- `platform.02` Prompt consumer dependency protection
- `platform.03` Provider connection bindings and entitlements
- `platform.04` CLI harness coverage and lifecycle
- `platform.05` Model comparison and benchmark records
- `platform.06` Private inference host adapter
- `platform.07` Reference repository review cursors
- `platform.08` Remote agent session bridge
- `platform.09` Subscription quota plans and reservations
- `platform.10` Persistent feature ownership
- `platform.11` GSD project interoperability
- `platform.12` Managed project maintenance catalog
- `platform.13` External PR screening and disposition workflow
- `platform.14` Task-class learning and cadence
- `platform.15` Cross-schedule forecasting
- `platform.16` Core dashboard compositions
- `platform.17` Subscription and instance usage accounting
- `platform.18` Personal insight and scorecard projections
- `platform.19` Domain alerts and readiness inventory
- `platform.20` Jira Datadog and repository management apps
- `platform.21` Direct peer identity and category policy
- `platform.22` Domain replication and conflict extensions
- `platform.23` Remote media execution
- `platform.24` Selective media sharing
- `platform.25` New-domain durability and migration

## Final integration acceptance

Run focused task gates when code changes, then one integrated console build and real browser journeys per completed wave. Validate registered API/tool/UI paths and restart persistence together. Exercise hosted isolation with two real isolated local homes and actual auth middleware; no production deployment or external messaging is implied by local implementation authorization. Record unavailable external/native journeys honestly. Finish only when the full backlog is qualified to its declared boundary, not when the first ten tasks pass.

## Progress records

Task records remain the qualification evidence. `python3 tooling/capability_progress.py` reports the current checkout without changing files. Add `--write` to synchronize backlog and KVX statuses from those records. This command does not run gates, publish commits, or deploy. Only explicitly locally qualified records count as done; partial and external-pending states remain in progress.

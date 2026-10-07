# Workflows

A workflow is a declarative graph of typed nodes. A controller schedules a run, dispatches
its admitted work, and records outcomes. The graph is declared in advance, but its
results can depend on model output, external systems, human decisions, and changing
runtime conditions. Declarative orchestration is not a guarantee of deterministic
external effects.

The implementation is under `runtime/gideon/automation/workflows/`.

## Core modules

| Module | Responsibility |
|---|---|
| `models.py` | Definition, node, run, state, and result records. |
| `service.py` | Definition and run operations, start admission, steering, and lifecycle requests. |
| `controller.py` | Per-run scheduling, queued control operations, preparation, and terminal state. |
| `tick.py`, `admission.py` | Frontier computation, dependency and concurrency admission. |
| `engine.py` | Actual stage, inference, action, and child-workflow dispatch. |
| `bindings.py`, `conditions.py` | Binding substitution and condition evaluation. |
| `store.py`, `journal.py` | Durable run data, outputs, and journal records. |
| `versions.py` | Definition snapshots, version selection, pinning, and rollback. |
| `mutations.py`, `checkpoints.py` | Typed edits, rewinds, forks, and pruning. |
| `human_input.py`, `confirmation.py`, `attention.py` | Waiting decisions, offered confirmations, and attention items. |
| `ownership.py`, `private_work.py`, `private_runs.py` | Execution identity, privacy inheritance, private receipt validation and retirement. |
| `provisioning.py`, `workspace.py`, `worktrees.py` | Workspace preparation and lifecycle. |
| `compaction.py`, `context.py` | Bounded prompt compaction, handoffs, and carryover. |
| `replay.py`, `projection.py` | Recorded-trajectory replay and user-facing state projections. |

These are source locations, not an exhaustive API. The service and registered action
provider contracts determine which operations are exposed.

## Nodes and scheduling

The current node vocabulary is defined by `NodeKind` in `models.py`:

| Kind | Behavior |
|---|---|
| `sequence`, `parallel`, `foreach`, `loop` | Container scheduling over children. |
| `stage` | Agent execution with the admitted session, tools, and model. |
| `infer` | Bounded model inference without a tool-using agent session. |
| `visualize` | Bounded generation of a widget specification. |
| `branch` | Selects a path from a condition or binding. |
| `transform` | Data reshaping using the supported expression or stored skeleton. |
| `action` | Dispatches a registered action provider. |
| `wait`, `gate` | Deadline or decision-controlled progress. |
| `subworkflow` | Starts a separately recorded child run. |

Container outcomes derive from their children. Dependency admission distinguishes
terminal predecessors from waiting work and accounts for skipped paths. Lane limits and
per-container concurrency limit different resources: the shared execution lane versus
an individual graph's fan-out. A blocked frontier is visible instead of being treated
as successful completion.

Action arguments belong under `config.with`, alongside the selected provider declaration.
The runtime supplies authoritative execution fields such as the actual run ID; request
payload labels cannot replace that identity.

## Bindings and outputs

Bindings include inputs, node outputs, iteration items, and declared secret references.
The binding language uses a closed set of operations; it is not arbitrary Python
execution. An unresolved reference differs from a legitimate null output. Input defaults
are applied when the run starts so the run records the values actually used.

Outputs and events pass through redaction and size handling before journal projection.
Large or binary results can be stored separately with an output reference. Redaction and
spilling reduce exposure; they do not justify putting credentials into arbitrary workflow
output. Inspect the actual journal schema when building downstream consumers.

Resume caches include execution epoch and input/spec identity. Rewinding changes the
epoch so superseded outputs do not become current cache hits. `gideon workflow replay`
uses recorded responses and clock observations to compare scheduling trajectories; it
does not replay external side effects or prove a live vendor operation succeeded.

## Versions, grants, and execution identity

Run starts select the actual definition version and retain approved version bounds.
Child workflows inherit those bounds and lineage. A template name alone cannot stand in
for consent to any future version of that template.

The host binds accepted execution origin separately from the effective work actor.
Scheduled work can retain its trigger origin while executing under an app's narrower
identity. Live installed state, declared app tier, and current grants are rechecked at
consumers. Children intersect the parent's limits; a stage or action cannot widen them
through an input field or an agent name.

Tool and action admission remains separate from permission to start a run. Approval
metadata, effective invocation risk, unattended ceilings, and provider readiness can
refuse dispatch. A recorded start or HTTP acceptance does not mean the first action
ran, and a provider acknowledgement is not a terminal completion result.

## Private runs

Temporary and Incognito origins retain their actual memory mode. Starting private work
requires a verified host-bound origin and a real native private-scope receipt. The adapter
validates that receipt against the native authority and stores only public scope identity
in run metadata; a serialized label cannot recreate the capability after restart.

Controller preparation validates the live receipt again. Private visibility and cleanup
use the actual accepted origin and run lifecycle. Scope retirement waits for related work
to finish; it is not triggered by an arbitrary client-supplied session name. Missing
proof or an unavailable native issuer causes refusal rather than conversion to a more
permissive memory mode.

A private execution scope permits that work's private resources. It does not grant
persistent memory reads or writes. App-scoped and shared memory require their own live
consent and native scope authority. See [memory](KNOWLEDGE_MEMORY.md) and
[chat sessions](CHAT_SESSIONS.md).

## Editing, waiting, and nesting

Live mutations use typed operations, version checks, and controller drain points.
A queued response means the edit was accepted for application, not that the new graph
has already run. Completed output requires the appropriate rewind before an edit that
would invalidate it. Confirmation is required where a cascade would re-execute work.

Subworkflows have their own run record and journal, with parent/root lineage. Nesting is
bounded before creating the child; the current engine cap is three. Forking or resuming
must retain unrelated run metadata, version consent, origin, and privacy constraints.

Total timeout bounds elapsed time; stall timeout measures lack of progress. Progress
callbacks matter for long-running work. Waiting for an owner decision is represented as
waiting, not successful completion.

## Verification and containment

Judge contracts, deterministic checks, artifact requirements, and actor-transition
rules constrain completion. A worker's claim alone is not independent evidence.
Configured checks can be unavailable, fail, or produce inconclusive results; report the
actual state instead of promoting missing verification to a pass.

Filesystem write-scope checks compare snapshots after work and report violations.
They detect changes; they are not preventive isolation. Process sandbox and egress
controls depend on the selected provider and operating system. The host provider cannot
promise arbitrary destination-list enforcement. Requested restrictions that cannot be
enforced may refuse execution; a provider declaration is not proof of containment.

## UI, events, and templates

Per-run events carry identity, sequence, and epoch information for frontend folding.
The run stream is separate from notification mute and quiet-hour policy. Event batching
preserves individual envelopes. Listing, details, events, traces, and output routes still
apply the run's actual visibility policy.

Bundled templates are package data under
`runtime/gideon/automation/workflows/bundled/`. User definitions and version snapshots
are separate. Availability and prerequisites depend on the chosen template and runtime;
there is no fixed template count guaranteed by this document.

See [workflow templates](../guides/WORKFLOW_TEMPLATES.md),
[loops](LOOPS.md), and [tasks and triggers](TASKS_TRIGGERS.md).

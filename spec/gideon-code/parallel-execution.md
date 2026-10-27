# Bounded vertical execution and semantic handoffs

Status: proposed normative execution design; every implementation packet remains pending. Spec authoring creates no implementation, deletion, publication, release or deployment authority. The source of task status is [spec.kvx](./spec.kvx); detailed packets are incorporated in that same native task table. The shared contract registry is [architecture.md](./architecture.md).

## 1. What a vertical packet means

A packet finishes one observable behaviour, including its explicit input, actual host consumer, policy/storage/process/provider adapters as applicable, output, failure path and recovery. A new class, data schema, copied algorithm, descriptor or direct constructor test alone does not qualify. For example, `explore-ranged-read` must traverse the host into a confined real file, return actual selected bounds and evidence, reject bad ranges/encodings, and preserve retrievable original bytes. Its parent later connects this same handler to a real model exploration journey.

`gideon-code inspect PACKET_ID [ARGS...]` is the canonical production consumer entrypoint before default-workflow integration. PACKET_ID is exactly the immutable task packet ID, not an undocumented shortened alias. Its descriptor declares arguments, required services and effects. Effects require explicit disposable fixture roots and the same effective permission checks as ordinary tool execution. Listing help is read-only. Unknown component, version, arguments or required service returns configuration error rather than a fabricated result.

Extraction/report packets instead expose the exact standalone script command named by their `consumer_path` and `do_N` fields. Those commands consume actual source trees, Git objects, wheel bytes, receipts or process observations. Their focused gates invoke those commands; merely importing their modules is insufficient.

Real local HTTP exchange of recorded provider bytes qualifies adapter protocol translation only. It does not demonstrate live model inference, authentication, remote support or coding competence. The selected live provider is qualified by the parent `local-turn` journey; an unselected provider profile remains explicitly unqualified until its own real endpoint receipt. Missing credentials cannot count as a remote pass, and an optional unselected adapter cannot force a new identity to be provisioned.

## 2. Two-level task graph

There are 66 bounded implementation packets and 30 retained macro integration/closure tasks. The macros are not a second implementation of packet features. Their job is to join already-qualified consumers into the ordinary CLI workflow, test one distinct cross-boundary scenario and record the resulting source identity. Every macro requires all packets whose `parent_task` names it. Its former dependencies remain, and packets ordinarily inherit those former macro prerequisites; the explicitly declared journal/result-store prerequisite exception below runs before the first provider turn, this avoids the cycle of a packet depending on its own not-yet-completed parent.

Every wave-close task requires all macro tasks in that wave. Every packet in the next wave inherits the preceding wave-close barrier through its parent prerequisites. Within one eligible wave, dispatch by `requires` and path compatibility, never by list position, a vague owner lane, or an agent's verbal readiness.

A dependency is satisfied only by a qualified receipt binding the exact required source, specification and semantic contract identities. Implemented code without a passing gate does not release dependants. An unavailable external capability is not a satisfied dependency merely because the task returned. An integration owner cannot overwrite that state with a success label.

### Mandatory assembly order

1. `freeze-inventory` consumes `freeze-tracked`, `freeze-import-edges`, `freeze-source-budget`; the latter two may run together after `freeze-tracked`.
2. `freeze-vectors` consumes `freeze-carried-corpus` and `freeze-archive-identity` in parallel after inventory acceptance.
3. `local-host-bootstrap` precedes config and all independent production consumers. `local-provider-config`, `session-journal-lock` and `session-result-objects` can proceed after host bootstrap; the durable stores explicitly do not inherit `session-resume` macro prerequisites. The stores and configuration together release the two protocol adapter packets in parallel. `local-turn` additionally requires both stores and assembles the first actual inference journey.
4. Event projection, budgets and cancellation can run concurrently after `local-turn`; `stream-turn` assembles them. The already-qualified journal/result stores are consumed without rerunning their gates; reconciliation follows streaming integration and `session-resume` joins continuity.
5. After `wave-1-close`, confined paths release ranged-read and search concurrently. Once `explore-tools` closes, CodeGraph and scoped-instruction branches run concurrently; their adapters remain separate files.
6. After `wave-2-close`, the exact-edit branch is policy → checkpoint → exact bytes → rewind. The exec branch begins only after `safe-edit`; sandbox → owned process → spooling. Receipt/baseline implementations can then run concurrently before repair controller closure.
7. After `wave-3-close`, standalone import reachability precedes the one wheel-build/install packet. The artifact macro consumes the built artifact; it must not rebuild it. Manifest preparation and archive recovery run concurrently after artifact acceptance, then approval preflight joins them.
8. `wave-4-close` presents an exact reviewable deletion packet. `cutover-durable-executor` remains blocked until a new manifest-bound approval and exact-path scope amendment; a planning permission is insufficient. Its parent owns the single final-layout build/smoke.
9. After `wave-5-close`, patch, background execution and interactive branches can proceed in parallel. Their own local dependencies serialize shared state changes.
10. After `wave-6-close`, child-agent and optional extension branches can proceed in parallel. Within delegation, packet → worktree → immutable submission → parent integration. MCP and scoped skills start independently; explicit hooks follow scoped skills.
11. After `wave-7-close`, manifest admission releases independent scoring and real baseline admission. `eval-corpus` freezes the admitted corpus before doctor/campaign planning. Four repair shards, three language shards and the safety shard then run in parallel on disjoint disposable workspaces, subject to provider budget limits.

Parallelism follows actual independent ownership. No minimum active-agent count is a correctness requirement; a narrow dependency frontier may legitimately allow fewer agents.

## 3. Host seam and shared-file ownership

`local-host-bootstrap` initially owns the independent package manifest, CLI front door, runtime assembly and service contract modules. Once it qualifies, the ordinary workflow's shared assembly files are written only by the declared macro integration owner. Macro integrations that share CLI/agent/manifest assembly paths are explicitly serialized by their own dependencies, including the wave-6 and wave-7 assemblies; feature packet work remains parallel. Macro owned-path sets exclude already-qualified feature modules/checkers and name only shared assembly plus their distinct integrated check. The host is deliberately small; this design does not authorize a marketplace, repository plugin loader, general dependency injection container, server, daemon or scheduler product.

Independent production packets own exactly their feature module, focused check and packaged descriptor. A descriptor is immutable distribution data beneath the installed package's registration resource directory. The loader validates its schema, exact `component_id`, major contract, allowed in-package module origin, factory, verbs, required services, configuration arguments and capabilities. It does not discover executables from cwd, user configuration, Python path injections, remote descriptions, skills or model text. Duplicate component/verb identities fail before import or effect.

The canonical handoff families are the `GC.*` contracts in [architecture.md](./architecture.md): `GC.Host/1`, `GC.Event/1`, `GC.Provider/1`, `GC.Tool/1`, `GC.Policy/1`, `GC.Workspace/1`, `GC.Effect/1`, `GC.Session/1`, `GC.Context/1`, `GC.Checkpoint/1`, `GC.Execution/1`, `GC.Child/1`, `GC.Extension/1`, `GC.Inventory/1`, `GC.Cutover/1` and `GC.Evidence/1`. Names such as `ProviderProfile/v1`, `ReadResult/v1`, `ChildSubmission/v1` and `RepairShardReceipt/v1` in packets identify views/messages within their declared `contract_families`, never parallel competing wire protocols. Their fields must be reconciled with the owning chapter before dispatch.

A descriptor extending an absent optional feature is not permission to import it at core startup. Core startup works with optional SDK/parser/MCP extras absent; selection of a missing feature returns an explicit capability error. The package owner assembles optional dependency metadata at integration. No feature worker concurrently edits the shared package manifest or lockfile.

## 4. Dispatch and lock algorithm

The orchestrator keeps an auditable dispatch record; this is execution bookkeeping, not a new running service. Before launching a worker:

1. Confirm implementation of this specific pending scope is authorized and every required task has a qualified source-bound receipt.
2. Freeze the selected task section, exact input commit/tree/dirty-overlay identity, current spec and chapter SHA256 values, semantic contract digest, provider/environment profile when relevant, budget and permitted actions. Unresolved placeholders are blockers, not defaults chosen by the worker.
3. Materialize an isolated writer worktree at that exact source. Workers cannot share the root index, working tree, policy authority, session writer or checkpoint journal. A worktree provides source isolation, not an OS security boundary.
4. Acquire exclusive ownership of every exact `touches` path, including tests, fixtures, descriptors, generated outputs and manifests. Path comparison uses normalized repository-relative names and rejects aliases/case-fold collisions on supported filesystems. A parent directory wildcard is not a lock or an ownership grant.
5. Compare this path set with every active task. Overlap is legal only after an explicit dependency has serialized the owners; two unordered packets with any shared path must not launch together. A read dependency grants no write ownership.
6. Record worker identity, worktree, packet digest, lock set, start/deadline, repair counters and expected consumer before dispatch. The worker receives no authority beyond the frozen packet and effective parent policy.
7. Release locks only after custody of the implementation/receipt or explicit failed/abandoned state is recorded. A disconnected worker is not presumed dead and cannot coexist with a replacement writer.

The orchestrator alone mutates task status and shared integration bookkeeping. Feature workers return receipts and exact result-tree identity; they do not concurrently edit the main specification task table. Child tasks write only their own task worktree. Publication from the repository root follows the authorized repository workflow once qualification is recorded and does not expand planning-only scope.

## 5. Worker start, implementation and return

Every worker starts by visibly stating mode, exact scope, allowed gates and stop condition. Its input also contains the five required delegation fields: exact files; single verify command; maximum one build plus one verify with at most two repairs; 30-minute cap; explicit stop condition.

Implement the whole bounded packet before formatting/building/testing. Then run the one declared focused build and verify command. A relevant task-scoped repair permits another invocation, at most two repair cycles, without resetting elapsed time. No broad lint, aggregate suite, sanitizer, browser matrix, duplicate review or confidence rebuild is added at packet level. Required positive, negative and recovery cases must actually be reached by that one focused gate; a test command name does not itself prove they ran.

Return the evidence line `revision, command, exit_code, log_path`, with an immutable receipt binding the packet/input/contract identities and result tree. A failed or timed-out gate returns implemented-not-qualified and preserves actual command, exit and complete log. Missing endpoint, unsupported selected backend, absent interface or incomplete recovery never becomes a skipped passing gate. A preexisting unrelated failure is recorded once in the feature finding ledger and not reinvestigated.

The orchestrator consumes successful worker exit/log/source receipts and moves on. It does not rerun successful worker gates, commission a duplicate reviewer or rebuild the worker's artifact. The macro assembles default workflow behaviour; the wave owner runs one declared distinct merged consumer journey. A newly changed integrated candidate is a new source identity, not a reason to rewrite old receipts.

## 6. Semantic contract pinning

A compatible Python signature does not establish a compatible contract. The canonical semantic digest covers:

- Every field name, type, required/default/unknown behaviour, byte encoding and allowed major schema version.
- Units and bounds: seconds versus milliseconds, bytes versus characters, one-based inclusive ranges, token estimates versus provider measurements, known versus unknown prices.
- Identity and ordering: session/turn/call/tool/event/process/source identities, uniqueness domains, sequencing, idempotent projections versus non-replayable effects.
- Status and failure: explicit refusal, interruption, timeout, spawn failure, unknown delivery, incomplete output, stale input, recovery-required and unsupported capability.
- Authority and durability: human approval origin, deny precedence, granted operation/preimage binding, intent/flush boundary, lock ownership, persistence before effects and retained recovery bytes.
- Replay/retry and resource semantics: what may repeat, what remains unknown, output limits, retained full-result references, parent/child budget consumption and cancellation propagation.

Both producer and consumer receipts name that same digest. Workers may not infer an omitted null meaning, silently convert a measurement, treat an empty result as proof of absence, downgrade confinement or turn a branch name into a source-tree identity. A mismatch blocks only affected work while independent disjoint packets continue.

## 7. Scope and drift protocol

When an exact owned file, required interface or contract meaning is insufficient, the worker records one concise finding and stops the affected path. It does not edit a neighbouring file, create a new private interpretation, add a dependency, regenerate a shared registry, weaken an acceptance clause or change frozen tests.

The integration owner may prepare a versioned amendment naming old/new semantics, exact additional files, affected producer/consumer packets, required migration/default handling, counterexample and recovery cases, source revisions and receipts invalidated. Direct user authority still bounds that amendment. Only after explicit plan reconciliation and applicable authorization does a new frozen packet launch. Previously accepted results remain immutable historical artifacts; compatible unaffected work is not reverified.

New generated immutable fixture/corpus files need a pre-start exact-path scope addendum. The approved cutover manifest additionally expands exact deletion/promotion paths by a spec revision before its task starts. No directory/glob in a narrative or candidate removal list substitutes for this exact scope. An absent manifest or unknown path blocks cutover.

## 8. Bounded release shards and evidence join

`release-campaign-plan` creates a frozen disjoint assignment; it does not itself run the entire campaign. Repair shards cover exactly `repair-001` through `repair-020` in four ordered five-case groups. Language shards each own ten frozen questions, one for Python, TypeScript and Rust. The safety shard owns the complete explicit mandatory case/variant set. The campaign manifest binds exact artifact/source/provider/model/policy/environment/checker identities for every shard.

Each shard has the same 30-minute ceiling. Five cases is a proposed initial partition, not a permission to exceed the ceiling. If any shard cannot finish its declared cases within its remaining budget, it stops unqualified with complete partial evidence; the owner must revise and split the still-required immutable case assignment before further dispatch. Replacement agents inherit remaining time and repair counters. A campaign cannot hide extra test matrices behind one command or continue in an unbounded background process.

Parallel shards use distinct real workspaces, private state directories, process groups and logs; a coordinator limits concurrent model requests against the existing provider profile and budget. They do not edit shared corpus/scorer/expected-answer files. Expected patches, hidden labels and harness checkers remain outside agent-write and model-context scope. A real configured provider is mandatory for repair/exploration; protocol recordings cannot substitute for solves.

Aggregation is a pure receipt join: reject overlap, omitted case/variant IDs, unexpected added cases, stale source/configuration, changed denominator or incomplete evidence. Publish attempted, solved, verified, blocked, invalid and failed counts, the explicit applicable denominator and known/unknown metrics. At least 90% exploration, zero silent wrong-target edits, at least 95% actual verification among non-environment-blocked repair runs and every mandatory safety/recovery pass remain mandatory. Held-out solve rate is measured and reported without inventing a baseline-free target.

`doctor-release` consumes these current shard receipts, exposes supported/unqualified installation behaviour and updates documentation. It does not rerun completed shards or launch a second whole campaign. `wave-8-close` reads closure evidence only.

## 9. Disconnect, retry and custody

The packet digest, permissions, owned-path reservation, source identity, intermediate artifacts, accumulated elapsed time and repair counters survive user disconnect and worker failure. The coordinator retains a live owner until task outcome is durably recorded. A replacement worker starts only after former write authority is revoked/observed terminated and receives the same inputs plus remaining budgets. It does not get another 30 minutes or two fresh repair cycles.

Submitting a child result freezes source custody separately from child lifecycle. Expiry, cancellation or retry cannot replace an accepted submission or destroy recoverable worktrees/checkpoints. Unknown effects are observed/reconciled or explicitly abandoned; they are never blindly replayed. A parent integration failure preserves original child artifacts and named conflicts.

The plan constrains drift through exact contracts, ownership and executable counterexamples. It does not promise mathematical absence of defects or use a high task count as evidence of capability. Structural authoring checks establish graph/coverage consistency; only later real runtime, failure, recovery and independent coding journeys can establish behaviour.

## 10. Packet index and proposed parallel frontiers

| Wave | Parent macro | Independent packet IDs (internal dependencies still apply) |
|---|---|---|
| 0 | `freeze-inventory` | `freeze-tracked`, `freeze-import-edges`, `freeze-source-budget` |
| 0 | `freeze-vectors` | `freeze-carried-corpus`, `freeze-archive-identity` |
| 1 | `local-turn` | `local-host-bootstrap`, `local-provider-config`, `local-openai-adapter`, `local-anthropic-adapter` |
| 1 | `stream-turn` | `stream-event-projection`, `stream-budget-admission`, `stream-cancel-lifecycle` |
| 1 | `session-resume` | `session-journal-lock`, `session-result-objects`, `session-reconcile-resume` |
| 2 | `explore-tools` | `explore-confined-paths`, `explore-ranged-read`, `explore-search-pagination` |
| 2 | `code-map` | `codemap-parse-cache`, `codemap-agent-consumer` |
| 2 | `context-instructions` | `context-scoped-instructions`, `context-compaction-pairing` |
| 3 | `safe-edit` | `edit-bound-policy`, `edit-checkpoint-custody`, `edit-exact-bytes`, `edit-conflict-rewind` |
| 3 | `safe-exec` | `exec-confinement-adapter`, `exec-owned-process`, `exec-output-spooling` |
| 3 | `repair-loop` | `repair-baseline-evidence`, `repair-command-receipts`, `repair-bounded-controller` |
| 4 | `artifact-boundary` | `boundary-import-reachability`, `boundary-isolated-wheel` |
| 4 | `prune-manifest` | `prune-canonical-manifest`, `prune-archive-recovery`, `prune-approval-preflight` |
| 5 | `prune-cutover` | `cutover-durable-executor` |
| 6 | `patch-transaction` | `patch-parse-preview`, `patch-stage-journal`, `patch-recovery-conflicts` |
| 6 | `background-exec` | `background-owned-registry`, `background-output-cancel` |
| 6 | `interactive-session` | `interactive-safe-projection`, `interactive-command-router`, `interactive-human-approval` |
| 7 | `worktree-agents` | `agents-bound-packet`, `agents-real-worktree`, `agents-immutable-submission`, `agents-parent-integration` |
| 7 | `mcp-skills` | `extensions-real-mcp`, `extensions-scoped-skills`, `extensions-policy-hooks` |
| 8 | `eval-corpus` | `eval-case-manifest`, `eval-real-baseline-admission`, `eval-independent-scoring` |
| 8 | `doctor-release` | `release-doctor-consumer`, `release-campaign-plan`, `release-repair-shard-1`, `release-repair-shard-2`, `release-repair-shard-3`, `release-repair-shard-4`, `release-explore-python`, `release-explore-typescript`, `release-explore-rust`, `release-safety-shard` |

The index is a reading aid. Exact dependencies, owned files, contract families, do steps, positive/negative/recovery cases and commands are normative in the packet table. A scheduler must validate the full graph rather than infer launch eligibility from this table.

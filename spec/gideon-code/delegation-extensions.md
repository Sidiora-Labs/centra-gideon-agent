# Bounded subagents, worktree custody, MCP, skills, and hooks

Status: **PROPOSED normative implementation contract, 2026-10-27. All product scenarios are UNRUN.** This chapter elaborates [the feature specification](./spec.kvx) and shares [the coding tools and security contract](./tools-security.md). Planning/authoring permission does not authorise launching the proposed runtime, creating worktrees, running providers, deleting platform files, or publishing source.

## 1. Product boundary and inspected source

The focused CLI's primary model remains the coding agent. A subagent is bounded delegated work under that process's supervision, not a second platform, gateway, scheduler, native-agent marketplace, remote fleet, or ACP-first runtime. Optional extensions SHALL be import-isolated and have CLI consumers in the same vertical slice in which they are introduced.

The existing [DelegationSupervisor](../../runtime/gideon/engine/subagent.py) supplies useful parent/session identity, queues, concurrency admission, usage roll-up, cancellation, reaping and orphan reconciliation. It is coupled to application grants, global configuration, multiple runners, and Hypermid contributions. Extraction SHALL preserve bounded lifecycle algorithms and relevant carried vectors while replacing those ownership adapters. The lean path SHALL not import the old supervisor merely to claim reuse.

The existing [MCP client](../../runtime/gideon/integrations/mcp_client.py), [stdio lifecycle](../../runtime/gideon/integrations/mcp_stdio.py), [discovery](../../runtime/gideon/integrations/mcp_discovery.py) and [read-only trust sealing](../../runtime/gideon/security/mcp_read_only_trust.py) provide reuse candidates. Trust sealing binds exact tool definitions rather than trusting a server name alone. The existing [native skill provider](../../runtime/gideon/extensions/skills/native.py) imports a marketplace abstraction; the lean loader SHALL retain local file consumption without bringing that marketplace dependency tree forward.

These are source observations, not operational qualification. No existing subprocess supervisor, trusted label, worktree name, success message or native session ID alone proves safe delegation, retained evidence, source custody, or correct integration.

## 2. Stable invariants and semantic ownership

| ID | Invariant | Producer | Consumer / enforcement point |
|---|---|---|---|
| DX-I01 | One current child generation owns an assignment; stale generations cannot submit/integrate. | Parent supervisor assignment writer | Child admission, receipt receiver, integration adapter |
| DX-I02 | Child capabilities are the intersection of parent authority and its explicit assignment. | Permission policy | Every child tool, MCP call, hook and spawned subprocess |
| DX-I03 | Every writer owns a distinct real worktree and exact write scope; common Git metadata is not generally writable. | Worktree manager | Filesystem/OS policy, child tools, result collector |
| DX-I04 | An immutable submitted artifact and receipt survive child exit/cleanup. | Supervisor result collector | Parent result reader and integration adapter |
| DX-I05 | A successful child gate is consumed once at its exact input identity; orchestration does not rerun it for confidence. | Child execution receipt writer | Parent scheduler, wave integration and release evidence reader |
| DX-I06 | Integration binds exact base, source delta, target state and permission; clean text merge is not behavioural qualification. | Integration adapter | Parent source state and merged-wave consumer journey |
| DX-I07 | Cancellation revokes admission/publication immediately and accounts for owned process cleanup separately. | Parent supervisor | Child tool gate, process lifecycle, submission receiver |
| DX-I08 | Optional extensions cannot bypass policy through annotations, helper scripts, output, or delegation. | Registry + policy | Runtime dispatcher and subprocess/MCP adapters |
| DX-I09 | Changed extension definitions/configuration invalidate stale trust and approvals. | Extension registry | Tool-list selection, permission gate, invocation revalidation |
| DX-I10 | Schema/context/output/time/cost budgets cover children and extensions; missing usage stays unknown. | Shared budget owner | Admission, context selector, event/accounting collector |
| DX-I11 | Source content and extension output are untrusted data, never host authority. | Context assembler | Model prompt construction, approval UI, policy writer |
| DX-I12 | No component task silently changes a shared producer/consumer contract while peers implement its previous revision. | Versioned contract registry in the spec packet | Implementation packet preflight and merged-wave contract assertion |

The terms `SHALL`, `MUST`, `SHALL NOT`, and `MUST NOT` are normative. A new task may refine an implementation detail but cannot waive an invariant because a local test passes. If a shared schema, failure meaning, ordering rule, budget unit or ownership boundary conflicts with another packet, the affected task SHALL record one bounded contradiction and stop on that interface; it SHALL not invent a private adapter that silently changes semantics.

## 3. Shared records and authority locations

All public domain events use the shared `EventEnvelope` in [tools and security](./tools-security.md): schema version 1, globally unique event ID, session ID, contiguous per-session sequence, optional turn ID, type, UTC time and typed payload. Child events have a separate child session stream and reference parent/assignment IDs; aggregation does not invent global ordering across streams. Event timestamps are observations; authoritative timeout/deadline comparisons use the supervisor's monotonic clock during a live process and a recorded restart policy after reboot.

### 3.1 Assignment packet v1

```json
{
  "schema_version": 1,
  "assignment_id": "host-id",
  "generation": 1,
  "parent_session_id": "parent-id",
  "task_id": "spec-task-id",
  "spec_digest": "sha256",
  "contract_digests": {"ToolResult.v1": "sha256"},
  "role": "explore|plan|implement|review",
  "mode": "PLAN|BUILD|QUALIFY",
  "objective": "one bounded deliverable",
  "read_paths": ["src/package"],
  "write_paths": ["src/package/a.py", "checks/test_a.py"],
  "source": {"repository_id": "repo-id", "base_commit": "oid", "input_tree_digest": "sha256"},
  "build_cmd": null,
  "verify_cmd": null,
  "max_build_runs": 1,
  "max_initial_verify_runs": 1,
  "max_repair_cycles": 2,
  "max_minutes": 30,
  "max_turns": 30,
  "max_output_bytes": 1048576,
  "token_budget": null,
  "cost_budget": null,
  "permission_digest": "sha256",
  "stop_condition": "concrete terminal condition",
  "allow_nested_delegation": false
}
```

The packet SHALL include the five visible task fields: exact files, single verify command (or explicit none for read-only authoring), build/repair limits, 30-minute cap, and stop condition. This schema's other fields establish identity, interfaces and budgets, not extra task scope. `max_minutes` may be lower than 30, never silently higher. A task requiring more than 30 minutes is split or explicitly revised before dispatch. Role names are defaults, not privileges: an `implement` label grants nothing outside the effective policy.

Command values SHALL be structured records with exact argv or shell command, cwd, allowed environment references, expected input selectors and command-definition digest in the final implementation schema. A string is shown above only as a compact illustrative value. A child cannot substitute a more convenient test, add an aggregate gate, or treat `verify_cmd=null` as permission to discover/run arbitrary checks.

### 3.2 Host-owned versus child-owned records

The parent supervisor owns assignment state, generation, worktree registration, budget reservations, process handles, cancellation, receipt ingestion and integration decisions. Children can propose findings, implementation claims and result summaries. They cannot write authoritative parent state by placing JSON into a worktree or printing a matching event.

Supervisor records are outside child-writable roots and use user-private storage. Local process authentication SHALL use an owned pipe or equivalent per-child capability channel; names/IDs/environment labels alone are not authentication. If records are private only by Unix permissions, child repository code SHALL not run with unrestricted same-UID access to the parent state. The qualified OS boundary must prevent that access, or the profile SHALL be reported unsupported. Directory naming is not an authority fence.

The parent is the only user-facing approval surface. A child receiving `approval_required` returns a structured pending operation to the parent; it cannot block the user's terminal with an independent prompt, enable unrestricted mode, read another credential store, or use a sibling's grant. Parent approval is still scoped to the child request and parent ceiling.

## 4. Decomposition into parallel vertical packets

Parallel work is allowed only for eligible, bounded packets with an explicit common input revision and non-conflicting writes. Every packet SHALL own a complete consumer slice: interface change plus producer/consumer wiring, its real fixture/assertion and the necessary scoped documentation when required. A packet containing only an unused abstraction, schema, helper, or test-only implementation is incomplete.

Before a wave launches, the parent SHALL provide each child a contract pin set and an ownership table:

| Interface | Producer owner | Consumer owner | Compatibility condition |
|---|---|---|---|
| `ToolResult.v1` and error codes | Runtime/tool contract owner | CLI, model dispatcher, session writer, child collector | Identical field/status/error meaning; no coercion from success text |
| Permission request/grant v1 | Security owner | File/exec/extension/child adapters | Same canonical argument digest, actor scope and revision revalidation |
| Observation/checkpoint v1 | Tool/recovery owner | Exact edit, patch, rewind and merge | Same digest/coverage semantics; no alternate preimage gate |
| Execution receipt v1 | Process owner | Repair loop, child gate and qualification reader | Actual exit/signal/timeout, source identity and retrievable log |
| Assignment/submission v1 | Delegation owner | Child runner, collector, integration | Current generation, exact source and durable artifact custody |
| Tool definition/trust v1 | Extension registry owner | Permission and context owners | Definition/config digest changes invalidate trust before invocation |

The implementation task graph SHALL assign actual packet IDs/paths to these owner roles. Two children SHALL not edit the same shared file opportunistically. Shared schema changes land in the designated producer packet and are consumed by dependent packets at a pinned revision; independent parallel consumers may implement against the frozen proposal only if the merge checks compatibility at the exact candidate. A discovered contract correction creates a visible spec/packet revision; the parent steers or replaces impacted packets explicitly.

An ownership table is not a substitute for real conflict detection. Preflight compares read/write sets, build/config roots, generated files, dependency lockfiles and shared fixtures. Disjoint source paths can still share a changed protocol, environment, generated file or test harness. Those semantic dependencies SHALL become explicit `requires` edges or a distinct merged-wave assertion. There is no automatic “no textual overlap means independent” rule.

## 5. Child admission and lifecycle

### 5.1 States

`REQUESTED → QUEUED → ADMITTED → MATERIALISING → RUNNING → SUBMITTING → SUBMITTED → RECEIPTED` is the normal state path. Alternative terminals are `REFUSED`, `FAILED`, `TIMED_OUT`, `CANCELLED`, and `OUTCOME_UNKNOWN`. `CLEANUP_PENDING` is a resource condition that may accompany any terminal result; cleanup does not erase receipt custody. Assignment outcome and integration outcome are separate dimensions.

The shared tool-effect lifecycle wraps spawn, integration and cleanup attempts. A spawn intent that is cancelled before actual launch SHALL not claim a child started. A process that launches but loses acknowledgement is reconciled by owned process identity; the supervisor SHALL not launch a replacement blindly under the same assignment.

### 5.2 Admission checks

Before allocating a worktree/process/model request, the supervisor SHALL validate:

1. Delegation is authorised by the user's current task/run scope and parent policy.
2. Assignment schema, current spec/task identity and dependency state are valid; no missing/unknown input is replaced by a guessed default.
3. One deliverable and exact path scope are declared. A read-only role has an empty write set and no arbitrary execution permission.
4. Parent/child policy intersection admits required capabilities. A child may request a smaller scope but cannot choose broader mode, environment, roots or network.
5. Current generation has no live duplicate assignment; capacity and budget reservation succeed atomically in the local supervisor state.
6. Source base/dirty-tree input exists and contract pins match available producer definitions.
7. The selected provider/model route already exists in authorised configuration; no credential provisioning or identity copying is implied by spawn.
8. The requested OS/process isolation profile can enforce worktree/private-state boundaries. Missing capability is a structured refusal, not a downgrade.

The default maximum is four concurrently running children per parent and one delegation level; effective limits may be configured lower. Queued children consume queue/state budget but not a provider slot. Nested delegation is disabled by default. Enabling it requires an explicit parent-authorised packet with reduced remaining budget, a registered lineage, and the same global concurrency accounting. There is no fan-out through external native CLIs that escapes accounting.

### 5.3 Running work

The child receives a fresh local session, private temp/output directory, explicit working directory, selected provider config references, and assignment packet. It SHALL not receive the entire parent conversation or unrelated credentials by default. Context selection provides task intent, relevant pinned contracts, required source references, applicable instructions, known qualification finding records and exact deliverable. Truncation/missing context is visible and may block the child; it is not silently omitted.

An active child emits bounded status with actual progress phase (`waiting_model`, `tool_running`, `waiting_approval`, `qualifying`, `submitting`) and last durable event. A heartbeat proves liveness only, not progress or correctness. Model text volume does not prove useful work. Parent UI shows running/queued/blocked states without converting silence into automatic resubmission.

The task implementation stage precedes its declared format/build/verify stage. Qualification runs one build and one verify invocation, with at most two task-scoped repair cycles after relevant changes. The child cannot start neighbouring fixes or follow recorded unrelated failures. Read-only PLAN packets have no build/verify permission merely because a runtime task type supports them. The 30-minute deadline begins when the child actually enters RUNNING; queue/provisioning time is recorded separately and subject to its own bound.

### 5.4 Steering and cancellation

Steering messages have message ID, assignment generation, text, permitted scope amendment references and delivery state. Repeated delivery has one logical message. Steering cannot increase capability or write scope through prose alone; amendments pass parent policy and produce a new packet/policy digest. An already-running tool is not silently edited midway; steering takes effect at its next safe boundary.

Stop/cancel is monotonic for a generation. The supervisor rejects new model/tool/extension/spawn admissions, closes approval requests, signals owned process groups and records cleanup. A cancel arriving before launch prevents launch even if queued allocation completed. A cancel after a submitted receipt preserves the immutable result but blocks unapproved integration/publication. Cancellation does not retroactively erase completed file effects or remote calls.

A deadline timeout stops work and records exact implemented-but-unqualified state plus the last actual command/exit/log. A worker that fails repeatedly is not respawned indefinitely. A fresh replacement may receive a new generation and preserved source/evidence under remaining authorised scope; no stale generation can resume writes or supersede its receipt. The product SHALL preserve useful incomplete artifacts before cleanup where safe.

## 6. Worktree materialisation and exact source inputs

### 6.1 Writer isolation

Every writing child SHALL use a distinct actual Git worktree created by the parent worktree manager. Read-only children may consume an immutable shared snapshot or read-only worktree. A plain folder copy labelled “worktree” is not acceptance. Worktree identity includes canonical path, common repository identity, worktree Git-directory identity, exact base commit, assignment/generation, initial manifest, and creation receipt.

The worktree manager uses structured Git argv, a unique internal name, safe private parent directory and no arbitrary repository hooks. It SHALL reject a path already owned by a user or another assignment. Worktree registration failures roll back only resources created by that materialisation effect. A half-created worktree is inventory/cleanup debt, not an admitted running assignment.

Children may edit admitted source paths but SHALL not write the main worktree, sibling worktrees, common Git object/ref/config/hooks directory, parent state, sealed logs, provider credentials or unrelated home directories. Linked worktrees share Git metadata; isolation cannot rely on different working directories alone. Native file tools block metadata paths. Arbitrary child subprocesses need OS-enforced read-only/hidden common metadata or a separate repository boundary. If the selected backend cannot enforce that, writer delegation is unsupported rather than “isolated by convention”.

In lean v1, Git metadata mutation/commits for submission are supervisor-mediated. Children produce source changes through admitted tools and may request a snapshot; they do not hold a broad Git push/commit capability. The parent collector creates immutable objects/receipts from the exact inspected child tree. This avoids claiming a branch naming convention fences shared Git state. A future child commit mode needs ref/metadata enforcement and a new contract.

### 6.2 Dirty parent source

A fresh worktree at HEAD excludes uncommitted user changes. The parent SHALL not pretend it contains the same inputs. When relevant parent state is dirty, the materialisation plan chooses explicitly between: (a) an immutable source-input snapshot including authorised tracked/untracked bytes, materialised into the child worktree without resetting the parent, or (b) a clean committed base with the omitted dirty paths and their impact clearly declared and accepted for this task. Relevant dirty content cannot be silently dropped.

The snapshot is a private input artifact, not an automatic public commit or stash. It names included/excluded paths, content digests, modes, base revision, initial dirty manifest and instruction versions. Secret/protected paths are excluded with explicit blocked-input implications. Untracked fixtures may be included only within authorised scope. The child diff is calculated relative to this exact input tree, not merely `git diff HEAD` when input includes overlay bytes.

All child submissions retain the base/input digest. Later parent changes do not move that input. A child observes fresh local edits it owns; it does not read moving parent source to “stay current”. Required upstream changes are delivered through a new explicit input/amendment and revalidation of applicable evidence.

### 6.3 Worktree cleanup

Cleanup begins only after the result has durable custody or the user explicitly abandons recoverable work. A dirty child worktree SHALL first produce an attributed recovery bundle/manifest if retention permits. Cleanup checks ownership identity and exact registered path; it cannot run broad `git worktree prune` or recursive deletion against arbitrary roots. A user opening/altering a child worktree creates a conflict/retention condition rather than licence to discard it.

Shared Git objects needed by accepted submissions SHALL be protected by owned refs or an independently retained bundle before a worktree/ref is removed. An object hash alone is not durable availability after garbage collection. Cleanup receipts include removed worktree registration, removed directory or retained reason, preserved object/bundle identities, remaining process status and any quota debt. A successful cleanup command without observed absence is not complete cleanup proof.

## 7. Submission and durable custody

### 7.1 Submission manifest

The collector SHALL produce an immutable result containing:

- Assignment ID/generation, parent and child sessions, task/spec/contract digests, requested/actual model identity and provider profile reference.
- Repository/base commit, exact initial input tree digest, submitted source tree/commit or bundle digest, ordered changed-path manifest, file modes/pre/postimage digests, and source availability reference.
- Implemented criterion IDs and locations as attributed child claims; incomplete scope, known limitations and unrelated findings remain explicit.
- Actual build/verify receipts: exact command/definition digest, cwd, source manifest, toolchain/environment/fixture identity where available, monotonic duration, exit/signal/timeout, selected tests/count availability, log reference/hash and retention owner.
- Executed build/verify/repair counts, deadline/budget consumption, provider usage with unknown separately represented, pending effects, unresolved journals and cleanup debt.
- Final structured outcome `implemented_qualified`, `implemented_unqualified`, `read_only_delivered`, `failed`, `cancelled`, or `outcome_unknown`. These are assignment outcomes, not ToolResult status replacements.

No path under the child's writable worktree can be the sole trusted source of the authoritative receipt. The collector validates against its own process/filesystem observations and sealed logs. A child-provided JSON “exit_code:0” is a claim, not execution evidence. A reviewer's source opinion is a finding, not a behaviour gate pass.

### 7.2 Acceptance and duplicate handling

Before acknowledgement, the parent checks current generation, expected task/source/contract identity, exact changed paths, absence of forbidden metadata/secret files, artifact integrity/availability, command receipt ownership and no unresolved mutation effects. A result can be received as incomplete/recovery material without qualifying it. Unexpected changed paths cause quarantine and explicit scope violation; they are not quietly excluded from a “successful” diff.

A submission key binds assignment, generation and manifest digest. Identical repeated submission returns the same receipt; a different payload under the same key is a conflict. A stale generation may upload explicitly attributed recovery material but cannot replace the current result. Accepted receipts are append-only; amendments produce a new result/version rather than mutating a previously accepted success.

Durable acknowledgement transfers custody from the worker/worktree to the supervisor result store. Worker timeout/exit/cleanup after acknowledgement does not release receipt custody, erase logs or make the task runnable again. No database/service is required: a local crash-safe private store and retained Git objects/bundles may satisfy custody, provided actual durability and restart scenarios are qualified. Worktree existence is not the result store.

### 7.3 Evidence consumption, not duplicate qualification

The parent SHALL consume the child's actual exit code and log reference without rerunning the same successful gate at unchanged inputs. It may reject a receipt for missing/wrong source, command or artifacts; that is receipt validation, not a new review agent. A receipt with unknown test count does not manufacture counts. A failed required gate, zero selected required tests, missing log or output-unknown effect leaves the task unqualified.

Integration runs one distinct merged-wave consumer journey selected in the spec. It does not blindly repeat every child gate. If merging changes a relevant producer/consumer input or requires conflict repair, record the affected evidence as stale and create the explicitly scoped repair/qualification packet required by the task graph. The prohibition on unchanged reverify does not allow reuse against a different source delta while pretending it is identical.

## 8. Orchestrator-only integration protocol

### 8.1 Preconditions

Only the parent integration adapter may propose applying a child's accepted delta to the target worktree. It checks that the user task permits local integration, the child generation/receipt is current, the submitted objects remain available, the declared write set is respected, and all required child task gates are satisfied. A child result never grants commit/push/release permission.

Integration intent binds receipt ID/digest, input base/tree, submitted delta digest, target repository/root, expected current HEAD and complete relevant dirty-tree manifest, requested path set, policy digest and conflict strategy. The host acquires the workspace mutation lock. Parent dirty/untracked changes are preserved; no `reset --hard`, `clean`, implicit stash or checkout overwrite is a valid conflict strategy.

### 8.2 Candidate and apply

1. Verify the target still equals the expected relevant state. If not, construct a new candidate against fresh state or return `target_changed`; do not overwrite intervening edits.
2. Compute a deterministic candidate tree/delta from the child's exact input tree and submitted delta against the target. Textual conflicts, delete/update collisions, path aliases, changed instructions/contracts, mode/symlink changes and out-of-scope generated files are explicit blockers.
3. Candidate creation is read/stage-only. No conflict-marker-bearing file enters the target worktree as an accepted result. A clean merge is a syntactic candidate, not proof of semantic compatibility.
4. Pass the candidate's exact path/diff set through the ordinary permission and observation/checkpoint gate. Parent code does not bypass evidence because a child read the files. The collector may provide host-origin immutable preimage evidence to a designated integration operation; that is explicitly distinct from falsely claiming the primary model observed them.
5. Apply through the same recoverable journalled transaction as ordinary patching. Target changes during commit cause recovery/refusal under the transaction contract. Terminal receipt includes actual pre/postimage identities, applied/skipped/conflicted paths, checkpoint, candidate tree and target state after integration.
6. Record one consumed/integrated receipt reference for that exact delta. Duplicate integration requests reconcile actual target postimages and return the existing receipt; they do not apply an exact edit twice.

Integration receipts separate `candidate_prepared`, `applied_to_worktree`, `committed`, `published`, and `wave_qualified`. In lean v1 only the first two are intrinsic to child integration. Commit/push may be performed separately under task authorisation and actual Git receipts; neither is implied. A successful `git apply` or branch update cannot set `wave_qualified` without the distinct required consumer journey.

### 8.3 Conflicts and partial failure

A conflict returns exact path/region identities and child/target/base references. The parent may assign one bounded repair packet in a new worktree at an explicit combined input; it SHALL not ask each child to rewrite the shared target independently. Conflict repair gets its own changed scope/contract pins and the relevant verification once. Unrelated child gates remain historical and are not rerun for confidence.

If target application partially fails, journal recovery owns the affected paths. The result is `outcome_unknown`/recovery-required until compensation or final state is established. The scheduler cannot mark the child integrated merely because the child's own result was qualified. A child can remain qualified and not integrated; the UI and evidence distinguish those states.

## 9. Provider, context, and cost accounting

Provider selection reuses the configured parent route unless the assignment explicitly selects another authorised available route. Requested model alias and actual provider-reported model identity are separately recorded; missing identity remains unknown. ACP/native-agent delegation is deferred, not a hidden fallback when the lean loop fails. A child SHALL not launch another coding agent binary to evade its task limits.

The parent reserves child concurrency, time, token and cost budgets before launch. Active child usage rolls up once by unique provider-call/effect identity. Retries and failed calls count where the provider reports usage. Unknown tokens/cost do not become zero. Parent totals separate own and child usage, known subtotal, unknown components and pending calls. Budget cancellation cannot erase already incurred usage.

Limits are enforced before the next model/tool admission; a single in-flight response may exceed an estimate, so output/token ceilings and observed overshoot are reported honestly. A configured cost ceiling cannot be claimed exact where provider billing/usage is unknown. A conservative token/call cap remains enforceable and the uncertainty is visible.

Child context uses the same instruction scope/provenance and compaction contracts as the primary. A child receives only applicable instruction snapshots; it cannot reinterpret a sibling instruction as authority. Raw result retrieval is lineage-scoped. Parent summaries cite the child's immutable result or findings and distinguish claims from receipts; copying an optimistic child summary is not proof.

## 10. Optional MCP stdio client

### 10.1 Scope and configuration

Lean v1 requires only a local stdio client. A production MCP server, HTTP/SSE service, OAuth broker, platform workflows/memory MCP, marketplace and hosted connector registry are outside this feature. Core import/start SHALL succeed without the optional MCP dependency installed. Enabling an extension imports only its declared optional extra and records its version.

Server configuration is explicit user-owned data: unique server ID, executable argv, cwd, environment-reference allowlist, confinement profile, network mode, permitted capability classes, initialization/call/idle deadlines and output/schema budgets. Repository config may propose a server but SHALL NOT launch it automatically. Editing a repository manifest does not grant process execution, environment access or network. Server launch uses the unified process gate and receipt.

No shell is used unless explicitly configured as an approved shell request. Executable resolution, server config digest, binary identity where available, working directory, optional dependency version and effective policy are pinned. The server is a child process with the same cleanup/credential boundaries as repository execution; “stdio” is transport, not trust.

### 10.2 Lifecycle and protocol boundaries

States are `DISABLED`, `CONFIGURED`, `LAUNCHING`, `INITIALISING`, `READY`, `DEGRADED`, `STOPPING`, `STOPPED` and `FAILED`. A server is tool-available only after the supported MCP library completes protocol initialization, capability negotiation and bounded discovery. Unsupported protocol versions, malformed messages, oversized output, broken pipes and initialization timeouts become typed extension failures; they do not crash unrelated native tools.

The implementation SHALL use the pinned MCP library's supported framing/handshake rather than invent a lookalike protocol. Stderr is diagnostic output and never parsed as protocol or approval input. Unsolicited server messages/prompts are untrusted bounded data. Capabilities that cause model sampling, user elicitation, resource subscriptions or other nested work are disabled in v1 unless separately specified with policy, budgets and lifecycle. Server-driven requests cannot silently initiate recursive inference or user prompts.

Disconnect/reconnect policy is bounded: at most one automatic reconnect after an infrastructure disconnect, with backoff and fresh initialization/discovery. An in-flight call whose outcome is unknown is never automatically replayed, even if a server claims idempotence. A new explicit invocation requires reconciliation or user/task acceptance of uncertainty. Server restarts invalidate live process identity and refresh tool-definition checks.

### 10.3 Tool identity, schema and trust

Registry names use a collision-free `(server_id, tool_name)` identity. Native names cannot be shadowed. Display aliases are not authority identifiers. Discovery rejects duplicate names within a server, unsupported schema constructs, schemas above 64 KiB per tool, more than 100 tools per server by default, and unsupported `$ref`/recursive loading. External schema references SHALL not trigger network or filesystem reads. A rejected tool is unavailable with a reason; the registry does not truncate its schema into a misleading usable definition.

Canonical tool digest covers name, description, complete input schema and annotations. A separate configuration revision covers executable/argv/env references/cwd/policy. The invocation binds both. Read-only annotations are untrusted until a user-owned trust record seals that exact definition and server configuration. Name-only or old-definition trust is invalid. A trusted label may inform approval classification within the host ceiling; it cannot grant out-of-root reads, network, credentials, publication or arbitrary external side effects.

Policy cannot infer all server behaviour from JSON schema. Unknown effects default to `external.effect` and approval. A read-only session SHALL not run a broad unconfined server merely because its selected tool advertises `readOnlyHint=true`. To qualify read-only use, launch confinement and actual approved server behaviour must satisfy the read-only boundary. Otherwise the tool is unavailable in that mode.

Tool definitions are rechecked before invocation; a `tools/list_changed` event, reconnect or config edit invalidates stale trust/approval before next effect. If a server changes name/description/schema/annotations between display and call, the host refuses the stale binding and reports exactly which digest changed without granting the newly listed tool. This preserves the existing definition-sealed trust concept.

### 10.4 Calls and output

Arguments pass the frozen schema and unified policy before send. Each call has host call/effect identity and protocol request correlation. Errors distinguish schema/policy refusal, MCP protocol failure, server tool error and unknown outcome. Server `isError`/result values are mapped into the shared ToolResult; printed “success” text never replaces them. Content blocks remain typed and bounded. V1 model consumption is text/structured data; images/audio/binary resources require an explicit supported content policy rather than accidental base64 inflation.

Returned resource URIs are untrusted identifiers. `file:`, arbitrary HTTP, command/data URI, and out-of-root references SHALL not be opened automatically. Resource retrieval is a separate gated operation with scope and budget; server output cannot smuggle a new filesystem/network capability. Returned content can be stored as a session raw result with provenance and redaction, but never as a trusted skill/policy/approval.

Default per-call timeout is 120 seconds and ceiling 600; default aggregate tool-definition context budget is 16,000 estimated tokens, with at most 32 active external schemas in one model request; default MCP output preview is 16 KiB and retained output is 1 MiB per call. The context estimator/version, selection order and omitted tool IDs are recorded. Overflow fails or selects an explicitly ranked subset with discoverable availability; it SHALL NOT silently claim every advertised tool was supplied. Native essential tools retain their configured reserved budget. A malicious server cannot evict safety instructions or task intent by advertising huge descriptions.

Shutdown cancels outstanding protocol requests where supported, stops the owned process group and records unresolved outcomes/cleanup debt. Exiting the CLI SHALL not leave a forgotten MCP daemon. A long-lived connection is scoped to one registered session/parent lifecycle; reconnect after resume requires explicit config/trust revalidation.

## 11. Local skills

### 11.1 Discovery and identity

The v1 loader discovers `.agents/skills/<skill-id>/SKILL.md` under the registered project and an explicit private user skill directory. User and project directories are separate origins. A project cannot redirect discovery outside its root with a symlink. Discovery reads bounded directory entries and metadata only; it never executes helper scripts, installs dependencies or calls a marketplace.

A skill record includes stable origin-qualified ID, declared name/description, root-relative source path, raw digest, parsed metadata, available supporting-file inventory and trust origin. Two origins with the same name are reported as ambiguous; the user/model selects an origin-qualified ID rather than silently letting a project shadow a user skill. Invalid metadata, missing entrypoint, oversize description or unreadable content produces a visible unavailable record, not a fabricated empty skill.

Discovery is limited to 200 skill entries, 4 KiB description metadata per entry and 8,000 estimated catalog tokens by default. Active skill content is limited to 64 KiB per skill and 16,000 estimated tokens total; selected exact content/digests and omitted/truncated supporting files are recorded. A required skill that cannot fit is a context-preparation block requiring explicit narrowing, not a partially loaded skill silently treated as complete.

### 11.2 Loading and instruction scope

The selected entrypoint is read before applying instructions. Relative references resolve from that skill's directory and remain under its admitted origin root. `../` escape, symlink escape, absolute private paths, external URIs and executable directives do not grant new access. Supporting files are read on demand through the same filesystem/tool boundary with individual size/context budgets. The loader does not recursively ingest an entire tree because a skill says “read everything”.

Skill content is task guidance at its origin trust level; it cannot override the user's direct scope, system/developer constraints, parent permission ceiling, no-fake rules, task qualification budget, or specification-owned shared contracts. A project skill cannot approve deletion/publication or change the selected provider identity. If a skill requires a scope-changing action, the host records a proposal and follows the existing user-authorisation contract; loading the skill itself is not permission.

If a user explicitly requests a skill, the runtime makes a bounded search of declared origins and reports exact missing/ambiguous state. It does not install arbitrary remote replacements. If a required instruction was not loaded because of a read error/limit, the task remains incomplete. A supporting reference read is attributed; no claim “skill followed” from the catalog description alone.

### 11.3 Scripts and drift

A script bundled with a skill is executable source, not passive trusted metadata. Invocation requires an explicit tool request under command/path/network/environment policy and a process receipt. Working directory and arguments are structured; skill markdown is never evaluated as shell. Scripts cannot access parent policy/provider homes or write beyond task scope merely because they live in a user directory.

The skill digest is pinned when loaded. If its files change before a proposed effect, the host labels the skill revision changed and reloads/reassesses affected guidance; an old approval does not cover new script bytes. For a known helper script, approval binds its digest, executable/argv, working directory, environment and effects. A task may record that advice changed but shall not mutate historical context to pretend it used the new version all along.

The lean loader SHALL not retain unused platform skill-curation, marketplace, recommendation, auto-improvement, personal-memory or remote publishing subsystems. New installed skill capability is deferred unless a future specification explicitly supplies its consumer and trust model.

## 12. Hooks

Hooks are disabled by default. V1 permits only explicit user-owned `before_tool` and `after_tool` command hooks; no scheduler, shell profile, plugin autoload or repository-installed event daemon is introduced. Repository files can propose hook configuration but cannot enable it. Each enabled hook records origin/config digest, executable/argv/script digest, cwd, environment references, allowed event types, capability scope and deadline.

`before_tool` receives a bounded JSON input on stdin containing event/tool identity, safe argument summary, actor, workspace identity and proposed capability set. It may emit a schema-valid `allow`, `deny`, or `note` result. `allow` is advisory and cannot override host denial; `deny` can narrow admission. It cannot rewrite tool arguments, change credentials, choose another tool, modify the request after approval, or approve itself. Mutation-capable hooks are ordinary separately approved effects and cannot run before the parent operation's policy check.

`after_tool` receives a bounded redacted receipt summary, never raw credentials, provider headers or complete environment. Its output is untrusted diagnostic content and cannot change the recorded tool outcome or qualify a failed command. Hook failure policies are explicit: before-tool default failure is deny; after-tool failure records extension failure and may stop subsequent admission, but it SHALL not relabel a completed mutation as no-effect failure.

Default deadline is five seconds, ceiling 30, combined output 64 KiB per hook, maximum four hooks per event. Hooks run in an owned noninteractive subprocess under the same confinement and network-off default. Hook subprocesses do not recursively trigger hooks; depth is zero for hook-generated events. Registration order is explicit and stable; cancellation prevents new hook launch. A hung hook is terminated/receipted under ordinary process cleanup rules. Hook output/unknown exit does not create an automatic retry loop.

Secrets are not inserted into hook stdin/argv/env unless an explicit separate capability authorises a named secret reference required by that hook; inference/admin/session secrets remain excluded. A hook executable changing on disk invalidates its approval/config digest. Shell escaping and terminal output controls follow the base execution contract.

## 13. Cross-component drift and failure containment

Native tools, subagents, MCP tools, skills and hooks share one registry identity model, one permission owner, one result/event schema, one raw-output retention owner and one budget owner. An extension may adapt protocol payloads, but SHALL not invent an independent success boolean, approval flag, untracked subprocess launcher or hidden memory store. An adapter is complete only when the CLI/model/parent consumers exercise its real path.

An extension failure is scoped: a malformed MCP schema can disable that server without preventing native source reads; a failed child leaves its result/cleanup state visible without deleting other children's work; a skill-loading error does not silently remove user task intent. A required capability's absence blocks the dependent task. The runtime does not skip it and announce the whole objective complete.

The parent merges status/evidence using immutable IDs and exact digests. A child changing a producer contract invalidates the pinned consumer expectation and triggers a recorded coordination decision. Hash equality establishes byte identity, not semantic compatibility; the merged-wave consumer assertions address semantic boundaries such as permission refusal, cancellation, output status, source freshness and integration ordering.

## 14. Concrete acceptance and adversarial scenarios

All cases are **UNRUN**. Each applicable implementation packet SHALL own exact assertion IDs/fixture inputs for the relevant rows; the main task plan selects its single verify command. Real temporary repositories, worktrees, subprocesses, local stdio peers and skill files are required. A local MCP peer implementing a deterministic real protocol operation is a test service, not a fake completion of the coding agent; its result cannot claim model capability or external-provider proof.

| ID | Given/action | Required observable assertions |
|---|---|---|
| DX-001 | Parent attempts spawn without exact files, verify disposition, cap or stop condition. | Admission refuses before allocation/model/process; diagnostics name missing packet fields. |
| DX-002 | Two concurrent spawn requests use same assignment/generation; lose first response. | At most one child/worktree/model lineage; repeat returns same registered state; no blind duplicate launch. |
| DX-003 | Read-only parent asks implement child or nested child with broader roots/network. | Intersection denies; no worktree mutation/provider call/helper launch for forbidden work. |
| DX-004 | Four running children plus another request; cancel one queued request while capacity frees. | Limits respected; cancelled queued child never starts; queue versus runtime durations separate. |
| DX-005 | Two implement children edit different exact source paths at same pinned input. | Real distinct worktrees, owned process identities and receipts; parent source unchanged until integration. |
| DX-006 | Child arbitrary shell tries sibling/main source, parent checkpoint/provider home and common `.git` writes. | Effective OS boundary denies each; path naming alone cannot pass; allowed child source edit still works. |
| DX-007 | Parent dirty tracked and untracked fixtures matter to child task. | Explicit input snapshot includes authorised bytes or task blocks/declares omission; child input digest is reconstructable; parent dirty bytes untouched. |
| DX-008 | Parent source moves while child runs. | Child input stays pinned; result identifies original base/tree; integration checks fresh target and cannot overwrite intervening change. |
| DX-009 | Deadline expires during build and child continues proposing tools. | New admission fenced; owned command stopped/cleanup debt recorded; implemented-unqualified state with actual command/log; no unlimited retry. |
| DX-010 | Child passes focused verify then parent receives its receipt. | Parent reads exit/log/digest once; no duplicate reviewer, rebuild or unchanged verify. A distinct merged-wave journey may run as registered. |
| DX-011 | Child prints forged success receipt or changes a receipt file in its own tree. | Collector uses owned execution/log evidence; forged/child-writable record cannot set qualified status. |
| DX-012 | Child submits changed path outside exact scope or includes a sensitive file. | Result quarantined/unqualified; no silent filtering into accepted success; no source integration. |
| DX-013 | Submit identical manifest twice; submit different manifest with same key; submit stale generation. | Same receipt for identical; conflict for changed; stale recovery attribution only; current result unchanged. |
| DX-014 | Kill child/remove its worktree after durable acknowledgement; run Git garbage collection. | Accepted source objects/bundle/log remain retrievable; receipt custody survives resource release. |
| DX-015 | Kill parent during submission before/after durable acknowledgement. | Restart reconciles one result/custody state; no loss of acknowledged artifacts or automatic reimplementation. |
| DX-016 | Child gate exits zero but selects no required tests, log missing or source digest mismatched. | Submission received as unqualified; no fabricated test count or done status. |
| DX-017 | Apply one accepted child delta twice with a lost first acknowledgement. | Actual postimage reconciles; one integration receipt/effect; exact edit is not applied twice. |
| DX-018 | Two children cleanly merge text but disagree on ToolResult status or policy meaning. | Pinned contract/merged consumer assertion catches incompatibility; no wave closure from text merge alone. |
| DX-019 | Parent target changes after candidate/approval; conflict markers or delete/update collision arise. | Integration refuses/replans; no markers or overwrite in parent; unrelated user work preserved. |
| DX-020 | Crash during multi-file integration commit; external editor changes one applied path. | Common journal recovery protects external bytes and reports recovery-required; child-qualified distinct from integrated. |
| DX-021 | Cancel before launch, during model wait, during tool execution, after submitted receipt and during cleanup. | Generation cancellation monotonic; no new effects/publication; completed receipt retained; owned process status and cleanup debt visible. |
| DX-022 | Same PID reused by unrelated process during orphan cleanup. | Identity mismatch prevents signal; unrelated process alive; stale handle reported. |
| DX-023 | Child emits repeated heartbeat/text without useful event; provider usage incomplete. | Liveness distinguished from progress; budget/deadline still enforce; unknown cost/tokens not zero. |
| DX-024 | Core starts with MCP extra absent and no extension config. | Native CLI/provider/read path available; no optional import, discovery process, marketplace or server dependency. |
| DX-025 | Repository adds auto-start MCP config pointing to shell script. | No launch until user-owned configuration and permission admit exact command/config; source text cannot enable it. |
| DX-026 | Real local stdio peer initializes, lists bounded tool and computes a deterministic repository statistic. | Actual library transport/process/call/result path used; tool schema identity, effect/receipt and policy visible. |
| DX-027 | Peer emits invalid/oversized framing, unsupported protocol, duplicate tool name or external `$ref` schema. | Extension fails/disabled with precise reason; no automatic external read; native tools remain usable. |
| DX-028 | MCP tool claims read-only but config is broad/untrusted; run from read-only parent. | Annotation cannot grant process/network/mutation rights; unsupported read-only profile unavailable rather than unconfined launch. |
| DX-029 | User sealed read-only trust; peer changes description/schema/annotations after display and before call. | Digest mismatch invalidates grant/trust; no stale invocation; change attributed and new definition not silently trusted. |
| DX-030 | MCP call performs effect then disconnects before reply; automatic reconnect enabled. | Original outcome unknown; at most one reconnect; original call not replayed; uncertain effect exposed. |
| DX-031 | Peer requests model sampling, user elicitation, URI read, recursive command or approval through tool content. | Unsupported capabilities denied; returned text remains data; no hidden inference/resource/permission effect. |
| DX-032 | Peer advertises huge schemas/descriptions and streams output above ceilings. | Limits enforced; required task/security context retained; omission/cutoff recorded; bounded memory/output; process cleaned. |
| DX-033 | Peer outputs forged native tool name, result ID, receipt or terminal approval prompt. | Namespaced identity and origin preserved; no native shadowing, cross-session result access or approval. |
| DX-034 | Parent exits/cancels with MCP calls active. | Owned server stopped or explicit cleanup pending; calls terminal/unknown as observed; no forgotten daemon. |
| DX-035 | User/project skills share name; project skill is symlink outside root or has oversized/malformed entrypoint. | Ambiguity/unavailability reported; no silent shadowing/out-of-root read/partial-as-complete instruction. |
| DX-036 | Required skill refers to `../` escape, home credentials, remote URI or an unlisted helper. | Reference access follows scope; forbidden bytes unread; helper not executed by loading markdown. |
| DX-037 | Load skill and later modify its helper/script before execution. | Digest change invalidates prior binding; script executes only as separately admitted process at known revision. |
| DX-038 | Project skill says enable unrestricted mode, publish source or expand task files. | Guidance cannot change authority/scope; host denial/explicit amendment path observed. |
| DX-039 | Catalog or active-skill content exceeds token budget. | Exact chosen/omitted identities and estimator known; required content absence blocks rather than silently applying only title. |
| DX-040 | Repository defines hooks but user has not enabled them. | No hook process/input/output or effect occurs. |
| DX-041 | Before-hook emits allow for host-denied operation, tries argument rewrite, or recursively invokes hook. | Host denial wins; mutation/rewrite rejected; recursion suppressed; no bypass. |
| DX-042 | Hook hangs, fails, exceeds output budget, returns malformed JSON or changes executable digest. | Bounded termination/failure semantics; before-hook denies; after-hook cannot rewrite completed effect; no automatic retry loop. |
| DX-043 | Hook input/output contains designated canary secret split across chunks. | Credentials excluded/redacted from normal output/model/logs; no raw provider/admin environment passed. |
| DX-044 | Parallel child pins old permission schema while producer task revises it. | Contract mismatch visible before merge/invocation; impacted packet paused/amended explicitly; no private silent adapter. |
| DX-045 | Disjoint child paths both alter a shared generated artifact/lockfile/API consumer through their build. | Semantic ownership/dependency detected; unexpected output changes quarantine; single owner or explicit integration packet required. |
| DX-046 | Real parent delegates a bounded repair using selected local skill and permitted MCP tool, then integrates. | Installed CLI/model/child/worktree/extension/permission/receipt paths used; one child verify consumed, one distinct wave journey; no hidden gateway/ACP/platform dependency. |

## 15. Completion and non-expansion rule

Delegation is complete only when the actual installed CLI can launch a bounded child, enforce its effective scope, retain its source/evidence after exit, consume its result without repeated qualification, and safely integrate an authorised delta. Optional extension completion requires a real local transport/file/helper consumer under the same boundaries. Mocked child-success text, an unused supervisor class, a passing direct-function test or a tool-list screenshot cannot satisfy those claims.

This chapter does not add an independent review campaign, remote fleet, task scheduler, gateway, server, marketplace, account provisioning, or automatic publication. All implementation remains driven by the explicit fine-grained packets and wave closures in [the feature specification](./spec.kvx), with one focused build/verify path per task, bounded repairs and honest partial outcomes.

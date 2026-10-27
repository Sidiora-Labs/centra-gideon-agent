# Extraction, packaging and cutover contract

Status: **SPECIFICATION ONLY. Every scenario in this chapter is UNRUN.** This chapter is normative for extraction and cutover tasks in [spec.kvx](spec.kvx). It neither authorises implementation nor supplies deletion approval. Paths under `lean/` and the final `src/gideon_code/` layout are proposed deliverables unless expressly identified as current source. A reference to a future checker does not establish that the checker exists.

## 1. Outcome and authority boundaries

The deliverable is one independently installable Python CLI coding agent. The executable owns its process, conversation, model connection, workspace policy and tool effects. Starting a gateway, inheriting a gateway token, importing the old runtime through an adapter or finding an old installation on `sys.path` cannot satisfy independence.

The extraction sequence is preserve → classify → carry contracts → implement one local consumer → extend the consumer → isolate the built artifact → present a concrete cutover packet → obtain manifest-specific approval → promote and prune. The old platform remains available until the independently installed artifact passes the required extraction journeys. Source archival preserves history; it does not keep platform machinery in the active product.

Four revisions must remain distinct:

| Identity | Definition | Permitted use |
|---|---|---|
| `baseline_revision` | Full commit object ID frozen by `freeze-inventory`. | Source provenance and original behavior. |
| `extraction_revision` | Commit identifying the extracted package and its accepted applicable-vector receipts. | Standalone acceptance. |
| `approval_source_revision` | Commit from which the exact deletion/promotion manifest is calculated. | User approval and cutover precondition. |
| `cutover_revision` | Commit recording only the approved removals, promotions and declared cutover changes. | Post-cutover artifact and recovery. |

The identities may coincide only where they actually refer to the same object. A dirty worktree needs a separately captured tree/overlay digest; quoting its HEAD is insufficient. No spec status, archive branch name, model statement or task receipt grants permission to change a different tree.

## 2. Verified source seams and inspection limits

The present root [pyproject.toml](../../pyproject.toml) declares the platform package, discovers `gideon*` under `runtime/`, adds `runtime`, the repository root and `checks/runtime` to pytest's Python path, and includes broad package-data patterns. The root [setup.py](../../setup.py) supplies `RuntimeBuild`: its normal build path runs Cargo for the Hypermid daemon and may run an npm/Vite console build. An editable install or a successful import from the repository therefore does not demonstrate the new package's build independence.

The extraction candidates include the provider contracts, read gate, dispatch planning, native loop/coding verbs, workspace policy, sandbox, checkpoint, compaction and CodeGraph consumer identified in [spec.kvx](spec.kvx). These are reuse candidates, not a promise that whole files can move unchanged. Initializers, local/lazy imports, default constructors, plugin discovery and configuration access are part of their dependency closure.

Source evidence identifies paths and behavior to preserve. It does not establish provider availability, OS sandbox support, a working wheel, an accurate percentage reduction or successful recovery. Those require the scenarios below and actual receipts.

## 3. Inventory contract — `freeze-inventory`

### 3.1 Enumeration and identity

`spec/gideon-code/inventory.json` shall be a versioned UTF-8 JSON object with `schema_version`, `baseline_revision`, Git object format, `enumerator_revision`, `counting_rules_version`, `generated_at`, `tracked_tree_digest`, `entries`, `dependency_edges`, `summary` and `unresolved`. `generated_at` is informational; deterministic content hashing excludes it through an expressly named canonical projection.

Enumerate the full tracked tree from the frozen commit using Git's object/tree view and a NUL-safe interface. Do not substitute a directory walk, ignore hidden files or exclude a language because it is planned for removal. Include symlinks and submodule entries; record LFS pointers as pointers and separately state whether their target bytes are available. Paths are repository-relative Git paths with `/` separators. Reject duplicate paths, absolute paths, `.`/`..` traversal and normalization collisions. Undecodable path bytes require an explicit byte encoding and block any tooling that cannot safely round-trip them.

For the working tree, record tracked modifications, staged changes, untracked paths and ignored personal/generated paths as a separate observation. Never ingest credential contents into the public inventory. Untracked files do not become eligible for deletion through their proximity to a tracked directory. Ordinary inventory is read-only and does not run project import hooks.

### 3.2 Per-path schema

| Field | Type and constraint | Meaning |
|---|---|---|
| `path` | Exact unique repository-relative path. | Source object identity within the tree. |
| `git_mode`, `git_object_id` | Mode and full object ID. | Preserve regular/executable/symlink/submodule semantics. |
| `sha256`, `size_bytes` | Hash of source blob bytes and byte count; explicit unavailable reason for unresolved external content. | Independent content identity. |
| `kind` | `production_source`, `check`, `fixture`, `packaging`, `ci`, `documentation`, `instruction`, `license`, `generated`, `asset`, `vendor`, `submodule`, `other`. | Counting and retention rules. |
| `disposition` | `retain`, `adapt`, `archive`, `remove`, `unresolved`. | Exactly one planned treatment. |
| `reason`, `consumer` | Nonempty reason; consumer task and runtime entrypoint or explicit archived historical consumer. | Prevents keeping unused foundations. |
| `destination_paths` | Exact future paths; empty for archive/remove. | Adaptations and promotions are reviewable. |
| `owner_task`, `requirement_ids` | Existing task ID and exact clause IDs. | Ownership and acceptance traceability. |
| `license_ids`, `attribution_paths` | Provenance links or an explicit unresolved status. | Legal retention follows copied code and fixtures. |
| `dependency_scan_status` | `complete`, `partial`, `not_applicable` with explanation. | A partial scan cannot count as zero dependencies. |
| `unresolved_reasons` | List, empty only after resolution. | Unknown paths and shared consumers remain visible. |

Adapt entries additionally record extracted symbols/algorithms, replaced ownership adapters, preserved behavior IDs and intentional differences. A destination cannot be claimed by two owners without an explicit shared-file integration contract. `remove` means proposed future removal, never permission to delete now.

### 3.3 Dependency-edge schema and reachability

Each edge records `from_path`, `from_symbol` when identifiable, `to_module_or_resource`, `resolved_path_or_distribution`, `edge_kind`, `activation_condition`, `source_span`, `scan_method` and `resolution_status`. Edge kinds include eager import, initializer import, function-local import, dynamic import, entry-point/plugin discovery, type-check-only import, subprocess, executable lookup, configuration/default lookup, package-data read, network service and native-library load.

The boundary checker starts from all shipped CLI entrypoints, imported package initializers, tool registrations, provider selections, optional-extra consumers and advertised doctor/interactive commands. Reachability includes failure and recovery branches, not just successful startup. An unresolved dynamic import or config-derived plugin name is a blocker until constrained by an explicit allowlist or replaced. A search reporting no textual `import gideon` is insufficient; assembled module names, `importlib`, entry points, `sys.path` manipulation, package resources and external executable launch paths must be accounted for.

Type-check-only imports may be excluded from runtime reachability only if they are never evaluated by supported runtime annotation/schema introspection. Build-time imports count against build independence. Old environment variable reads, process-global provider registries and fallback home directories are dependencies even when their Python import edge has been removed.

### 3.4 Inventory invariants

Every tracked path has exactly one entry; the reconstructed tree inventory matches the frozen tree. Every retain/adapt entry has a named consumer. Every remove entry has no final active consumer. Every copied license/attribution obligation has a retained destination. Every dependency edge is resolved, intentionally archived or explicitly blocking. Summary counts are computed from entries, not hand-maintained prose. Inventory changes after baseline produce a new manifest revision and change record; they do not rewrite the old snapshot.

`wave-0-close` consumes the inventory's complete classification and dependency disposition. It does not start extraction merely because JSON parses.

## 4. Carried vectors — `freeze-vectors`

### 4.1 Preserve bytes and semantics separately

`spec/gideon-code/carried-manifest.json` shall pin the baseline and inventory digests. Each vector record has `vector_id`, source revision/path/blob ID/SHA256, copied path/SHA256, source test or fixture selector, original import closure, behavior clause, disposition, adapter path/digest, applicable provider/platform variants and owning task. Preserve original source and fixture bytes; an adapter lives outside the immutable corpus.

Classify each record as:

| Disposition | Meaning | Qualification effect |
|---|---|---|
| `carry-executable` | Original assertion/fixture is executable against the extracted real implementation through a narrow adapter. | Required extraction contract. |
| `carry-input` | Immutable protocol/fixture bytes feed separately named real-adapter assertions. | Proves serialization/behavior only, not model reasoning. |
| `archive-contract` | Platform-specific or mock-dependent original retained as historical source evidence. | Cannot count as standalone acceptance. |
| `superseded-contract` | An intentional new contract changes an old assertion; original remains immutable and linked to the replacement requirement and case. | Requires explicit spec decision; excluded from unchanged parity denominator. |
| `unresolved` | Applicability or closure is unknown. | Blocks the relevant extraction boundary. |

Assertions are not silently rewritten or omitted to make a port green. Where a source file mixes portable and platform assertions, retain the whole source bytes and record exact selected assertion IDs. A selection record is reviewed as a contract change, not hidden in pytest filtering.

### 4.2 Initial source candidates

The following current files are candidates to classify, not a claim that every assertion is portable or fake-free:

| Source | Behavior to inspect | Consumer task |
|---|---|---|
| [test_pre_edit_read_gate.py](../../checks/runtime/test_pre_edit_read_gate.py) | Observed content, stale preimage and pre-edit evidence. | `safe-edit`, `patch-transaction`. |
| [test_native_workspace_access.py](../../checks/runtime/test_native_workspace_access.py) | Workspace roots and path-access decisions. | `explore-tools`, `safe-edit`, `safe-exec`. |
| [test_native_concurrent_dispatch.py](../../checks/runtime/test_native_concurrent_dispatch.py) | Conflict-aware dispatch and result ordering. | Extracted runtime consumers. |
| [test_turn_checkpoints.py](../../checks/runtime/test_turn_checkpoints.py) | Checkpoint ownership, conflict refusal and rewind. | `safe-edit`, `wave-3-close`. |
| [test_context_compaction.py](../../checks/runtime/test_context_compaction.py) | Tool-pair integrity, bounded reduction and retained intent. | `context-instructions`. |
| [test_provider_stream_cutoff.py](../../checks/runtime/test_provider_stream_cutoff.py) | Incomplete provider streams and truthful termination. | `stream-turn`. |
| [test_prompt_cache_wire_translation.py](../../checks/runtime/test_prompt_cache_wire_translation.py) | Provider-specific request/cache translation. | `local-turn`, `stream-turn`. |
| [test_codegraph.py](../../checks/runtime/test_codegraph.py) | Existing parser/index symbol semantics. | `code-map`. |
| [test_code_agentic_tools.py](../../checks/runtime/test_code_agentic_tools.py) | Existing tool availability contracts, including deliberately absent dedicated tools. | `explore-tools`, later tool extensions. |

Session-store acceptance must use actual new disk persistence; a dashboard session test is not a new SessionStore vector. Hypermid golden vectors remain archived unless the exact algorithm is ported. Original tests relying on mocks or `ScriptedProvider` remain historical contract material; they cannot qualify real model execution. Reuse of a fixture does not transfer the original test's qualification status.

### 4.3 Adapter restrictions

An adapter may translate namespace imports, constructor ownership injection, public type names and transport-independent input representation. It must call the shipped new code and use real files, Git, processes and provider adapters as applicable. It may not replace the target algorithm, return fixed expected values, patch the production outcome, suppress failed assertions or import the old implementation to obtain answers. Test-time path injection into `runtime/gideon` is prohibited.

The manifest records every translation and any observable difference. If translating an assertion requires understanding its entire old platform closure, mark it unresolved or archive it with rationale, then add an independently named new real-path scenario. Do not claim unchanged vector parity for that new scenario.

### 4.4 Immutability and applicability

At each consuming task, recompute the selected corpus hashes and adapter digest. The applicable-vector set is frozen before the task runs. Applicable vectors must execute against the extracted implementation once at the declared boundary. A newly discovered incompatibility creates a finding and spec decision; deleting the vector or changing its expected result is not a repair.

The final report gives counts by disposition and provider/platform variant: eligible, executed, passed, failed, blocked, archived and superseded. Report denominators. A 100% pass over two selected assertions cannot be presented as passing an entire historical suite.

## 5. Build and import independence — `local-turn`, `artifact-boundary`

### 5.1 Package contract

The staging package has its own `lean/pyproject.toml` with explicit `src` discovery for `gideon_code` only, a versioned Python support range, explicit runtime dependency bounds and an entrypoint pointing directly into the new package. It must not execute root [setup.py](../../setup.py), include `gideon` packages, vendor the old tree, install legacy launchers, run Cargo/npm or bundle console/Hypermid artifacts. Source and wheel package-data includes are allowlisted. Build dependencies are listed separately from runtime dependencies.

A minimal base install starts help, configuration validation, session inspection and a missing-provider diagnostic without optional extras. An advertised provider requires its explicit extra or declared mandatory dependency. Missing optional SDK/parser/MCP packages produce typed actionable errors only when their consumer is selected; they never crash unrelated startup. Qualified capability matrices identify exactly which provider extra and grammar packages were installed.

### 5.2 The isolated artifact experiment

`artifact-boundary` builds one wheel using its declared build command. Its verification consumes that exact wheel and records its filename, SHA256, metadata and RECORD entries. Do not build again inside verification or substitute an editable install. A dirty build input is identified by tree/overlay digest in addition to commit.

The experiment shall:

1. Create a disposable environment outside the old checkout. Install only the selected wheel and frozen declared dependency set; do not install the old distribution, root package or developer extras by convenience.
2. Materialise a separate fixture Git repository and explicit campaign configuration. Use fresh XDG config/state/cache/temp roots and an allowlisted environment. Remove inherited `PYTHONPATH`, `PYTHONHOME`, pytest path injection, developer import hooks, legacy `GIDEON_HOME`, gateway tokens and platform configuration. Supply only the authorised provider credential reference and required platform variables; never print values.
3. Make the legacy checkout inaccessible to the child execution boundary. Being elsewhere on disk or deleting it from `PYTHONPATH` is insufficient. Use a qualified OS isolation primitive or a disposable VM/container whose mounted/copied inputs exclude the legacy tree. Do not rename or chmod the user's actual checkout to achieve this.
4. Verify a direct old-path access attempt is denied/missing from within that boundary. Record the mechanism and observed mount/access result. If the mechanism cannot establish absence/inaccessibility, the case is BLOCKED.
5. Invoke the installed executable by resolved absolute path from the fixture repository. Record interpreter path/version, distribution metadata, `sys.path`, loaded module origins, loaded native libraries where relevant and child executable identities. No origin may resolve to the old checkout, legacy distribution or injected test source.
6. Run the declared installed-artifact read/edit/check/resume/rewind journey using one real configured provider. Its independent checker examines actual fixture content, Git diff, process exit/logs and persisted session/checkpoint state. Model prose cannot serve as the oracle.
7. Run applicable carried contracts through the installed new package, preserving the same inaccessible-source boundary. Adapter/assertion code may be supplied as explicit harness inputs; it may not expose the old executable implementation or answer patch.
8. Verify no legacy home/config/state paths were created or read, no gateway/daemon was started and no unapproved network destination was contacted. Keep provider traffic and any explicitly permitted dependency acquisition separate from coding-workspace network permission.

The harness controller may inspect source or wheel metadata outside the child boundary. Its own imports cannot be used as evidence of installed-product independence. A temporary Python environment alone does not constitute an OS security sandbox.

### 5.3 Import-boundary proof

Independence requires both static closure and observed execution origins. Static closure covers unexercised lazy/optional branches; runtime evidence catches resolution surprises. The union of selected extras and supported command paths is enumerated explicitly. A minimal install pass cannot qualify MCP, optional grammars, a second provider or another OS. Unresolved dynamic edges and undisclosed filesystem fallbacks fail the boundary.

`check_boundary.py` shall report forbidden packages/resources and the shortest reachable path from a shipped consumer, along with the scanner limits. It must not claim complete independence while reporting a partial scan. The old package name may appear in historical attribution or test expectations; text presence alone is neither an import nor an error. Executable import/resource reachability is the criterion.

## 6. Exact deletion and promotion manifest — `prune-manifest`

### 6.1 Manifest schema

`spec/gideon-code/deletion-manifest.json` is a review artifact with `schema_version`, source/spec/inventory/carried-vector/boundary-receipt identities, `manifest_id`, `deletions`, `promotions`, `replacements`, `retained_protected_paths`, `archive_refs`, `budgets`, `preconditions`, `recovery_plan_digest` and `approval_payload_sha256`.

Each deletion is an exact tracked path with source blob ID, SHA256, Git mode, inventory disposition, reason, owning task and replacement/archive provenance. Directories, wildcard patterns, glob expansion, path prefixes and a recursive delete command are not valid deletion entries. A submodule is a distinct tracked entry; deleting its worktree contents is outside a regular-file deletion. Symlinks are unlinked as symlinks; their targets are never traversed.

Each promotion has exact source/destination paths, source bytes/mode, expected destination state (`absent` or named preimage hash), transformation type and expected output hash. Byte-preserving moves are distinguished from path/content rewrites. Replacements of root manifests, README, CI, packaging or instructions are exact reviewed postimage changes, not implied by moving a directory. All final references and commands use final-layout paths.

Protected retained paths include licenses/NOTICE/required attribution, applicable immutable vectors/manifests, the active spec and evidence policy, any instruction source required to regenerate generated files, and user-owned/untracked content. A protected-path change requires an explicit reviewed entry; it cannot be smuggled through a parent directory removal.

### 6.2 Deterministic digest

The approval payload contains schema version, source/spec revisions, exact path operations and pre/postimage hashes, prerequisite receipt digests, archive commit identities, budgets and recovery-plan digest. Canonical bytes are UTF-8 JSON with object keys sorted, separators `,` and `:`, no insignificant whitespace, `ensure_ascii=true`, no NaN/Infinity, and arrays already sorted by exact UTF-8 path bytes and operation type. Duplicate keys and duplicate/conflicting operations are rejected before canonicalization. The payload excludes only its own digest and the later approval record; the exclusion list is versioned and exact.

Compute SHA256 over those bytes. The display manifest may be pretty-printed, but the approval packet includes the canonical digest and human-readable exact operation counts. Hashing an unspecified file representation or hashing only the deletion list while leaving promotions mutable is insufficient.

Any new source revision, changed path bytes/mode, path operation, destination collision, relevant prerequisite receipt, recovery instruction or archive identity changes the approval payload and invalidates approval. A tool may explain identical content under a new commit, but it may not automatically carry permission to that new source revision.

### 6.3 Concrete approval packet

Before requesting approval, provide:

1. The source revision, manifest digest and spec revision.
2. Exact delete/promote/replace counts plus the complete reviewable path list.
3. Retained legal/instruction/vector paths and their destinations.
4. Installed-wheel digest, independence result, applicable-vector result and installed coding journey receipt.
5. Archive local/remote ref names, full commit IDs and the observed remote identity.
6. Before/after projected budgets with counting rules; explain unknowns rather than filling guessed numbers.
7. Recovery procedure, expected final artifact/entrypoint and interruption behavior.
8. A statement that personal configuration, old sessions, home directories, credentials and unrelated work are outside deletion scope.

The user response must explicitly approve the digest and source revision. Preserve the user message's identity/reference and exact approval scope in `cutover.kvx` or a linked approval record; never forge a user identity or turn an agent-authored recommended answer into approval. A generic instruction to implement extraction, a prior YES, a timeout or a successful gate does not approve an unpresented manifest.

## 7. Archive, cutover and recovery

### 7.1 Archive readiness

Before presenting deletion approval, create and verify the specified immutable pre-prune tag and `archive/gideon-platform` branch under implementation publication authority. Resolve each to the exact source commit. Push only to the configured authorised remote and inspect its returned refs; a local branch or command exit alone is insufficient. Ref-name collision with a different object blocks overwrite. Preserve a manifest of ref names, commit IDs, remote repository identity and observation time without credential-bearing URLs.

History availability requires Git object reachability, submodule/LFS/resource completeness and retained licenses. If an external object required for recovery is unavailable, label the recovery gap and block pruning where the product cannot be reconstructed. Do not copy secrets into Git to make an archive complete. An archive establishes recoverable source, not automatic restoration of personal state or historical running services.

### 7.2 Cutover preflight

Immediately before `prune-cutover`, resolve HEAD/tree, index and worktree identities, compare all operation preimages and expected absent destinations, verify archive refs, approval payload and prerequisite receipts. Refuse when any operation overlaps unrelated work. Prefer a dedicated clean cutover worktree at the approved source revision; never clean/reset the user's original checkout to make preflight pass.

The final operation set is exactly the approved manifest. Root packaging/CI/instruction replacements are included in its reviewed postimages. Generated instruction pointers are changed through their source/generator only if that source exists and the cutover task explicitly owns it. Missing generated-source tooling is a blocker, not permission to invent or silently hand-edit it.

### 7.3 Apply and commit

Stage prepared promotion/replacement outputs before unlinking approved paths. Maintain a cutover journal outside the deletion set containing operation sequence, pre/postimage digests and completed steps. Recheck preimages immediately before each effect. A failed operation stops the cutover; it does not continue with a partial tree advertised as final.

One reviewable cutover commit contains all approved removals/promotions and exact supporting changes. Publication follows the explicitly authorised implementation workflow and guards. It must not incidentally capture unrelated dirty files through `git add -A`; if unrelated content exists, isolate the work before staging. The final wheel is built once from the final layout, then one declared installed consumer smoke verifies that artifact. The receipt records its source tree and wheel digest independently of the earlier staging wheel.

Post-cutover no active launcher, CI job, dependency manifest, documentation command or import may refer to a removed executable path. Historical source references remain historical and are labelled. Byte-preserving move maps connect carried provenance and staged package receipt identities to final paths; build metadata changes require a new artifact identity.

### 7.4 Interrupted and failed cutover

| Boundary | Required response |
|---|---|
| Before first filesystem effect | Stop safely; leave all files unchanged and record refusal. |
| During staged copy/promotion | Recover from the journal after checking preimages; delete only task-owned temporary outputs. |
| During removals before commit | Stop with `recovery-required`; reconstruct task-owned source blobs in the isolated cutover worktree from the frozen tree or abandon that worktree after preserving evidence. |
| Commit exists, final smoke fails | Keep the commit and failure receipt visible; use one scoped repair within limits or report implemented-not-qualified. No claim of cutover completion. |
| Published cutover must be reversed | Prepare a reviewed revert/recovery commit from the actual current head, resolve later conflicts explicitly and publish through authorised workflow. Never reset a user's working tree. |
| Old personal-state schema cannot be read by the new or recovered package | Retain state unchanged, diagnose incompatibility and require an explicit qualified migration; source rollback does not downgrade data. |

Recovery is demonstrated in a separate disposable checkout by resolving the recorded archive commit and verifying source blob identities plus the declared historical packaging prerequisites. It need not resurrect excluded services or fetch arbitrary dependencies. A recovery check cannot silently claim real old-provider or platform-service health.

## 8. Source, dependency and artifact budgets

At pruning, production Python code is limited to **15,000 physical lines and 80 modules**; direct mandatory runtime dependencies are limited to **six**. These are proposed acceptance budgets, not current measurements. An over-budget design is revised explicitly before the affected implementation starts; it is not reclassified as checks or generated data merely to pass.

Counting rules are frozen by `freeze-inventory`: count all `.py` production files distributed in the wheel, including `__init__.py`, generated/vendored production code and adapters; count every physical newline plus one final unterminated line, including blanks/docstrings/comments. A module is one such file. Runtime code shipped as a string, template, executable asset or embedded source is reported in additional bytes/lines and cannot escape the budget by changing its extension. Exclude checks, immutable historical fixtures and documentation only when they are outside the installed production path and not executable runtime dependencies.

Dependency counts distinguish direct mandatory distributions after target-environment marker evaluation, optional-extra distributions by named consumer, unique transitive distributions, native libraries, build-only tools and test-only tools. An optional dependency imported at base startup is mandatory in practice. Duplicate constraints on the same normalized distribution count once for direct count but conflicting constraints fail. A dependency with no consumer is removed or justified before admission.

Report per-platform/minimal/extra profiles, production source bytes/lines/modules, wheel compressed/uncompressed bytes, package-data bytes, installed distribution count and transitive closure, baseline count and the exact delta. Do not assert percentage reduction unless both numerator and denominator use the same declared units and scope. A wave adding a dependency or exceeding the budget requires a versioned design note naming consumer, alternatives, code/dependency cost and affected acceptance. A changed budget never retroactively passes an old failed receipt.

## 9. Extraction scenario registry

All cases are **UNRUN**. Exact assertion selectors become implementation task manifests; a component result cannot close the full installed-artifact or cutover case.

| ID | Task / clause | Scenario | Required independent observation |
|---|---|---|---|
| EX-001 | `freeze-inventory`; `freeze.1` | Inventory tracked hidden files, executable files, symlinks, submodules, unusual path names and untracked user work. | Exact tree coverage, modes/hashes round-trip, untracked content outside deletion set. |
| EX-002 | `freeze-inventory`, `wave-0-close`; `freeze.1` | Leave an unresolved/shared classification or unresolved dynamic edge. | Boundary remains blocked and identifies the source consumer; JSON existence cannot pass. |
| EX-003 | `freeze-vectors`; `freeze.2` | Alter one original fixture byte or selector after freezing. | SHA256/selector mismatch rejected before parity execution. |
| EX-004 | `freeze-vectors`, `artifact-boundary`; `freeze.2`, `prune.2` | Namespace adapter attempts legacy import or replaces an algorithm result. | Forbidden closure is rejected; only real extracted target can qualify. |
| EX-005 | `freeze-vectors`, `wave-0-close`; `freeze.2` | Mix portable, mock-dependent and incompatible old tool assertions. | Each assertion has an explicit disposition; archived/superseded items cannot inflate parity. |
| EX-006 | `artifact-boundary`; `local.1`, `prune.1` | Add function-local/dynamic import, entry-point fallback or old config-home read. | Static closure identifies each path or reports unresolved and blocks. |
| EX-007 | `artifact-boundary`; `local.1`, `prune.2` | Contaminate a developer path/site import and observe module origins. | Isolated child excludes contaminated origins and refuses legacy dependencies. |
| EX-008 | `local-turn`, `artifact-boundary`; `local.1` | Base install without extras, then each advertised extra. | Base starts; selecting missing extra diagnoses it; qualifying one extra does not qualify another. |
| EX-009 | `artifact-boundary`; `prune.2` | Build once, remove old-tree access from execution boundary and complete coding/resume/rewind. | Exact installed wheel works; denial probe, real provider trace, file/Git/session oracles recorded. |
| EX-010 | `prune-manifest`; `prune.1` | Manifest uses globs, duplicate operations, path traversal, untracked path or protected license omission. | Validator refuses every invalid operation set without deleting. |
| EX-011 | `prune-manifest`, `prune-cutover`; `prune.1`, `prune.3` | Change a promotion, source preimage, source revision or prerequisite after digest calculation. | Approval payload changes and old approval cannot authorise new operations. |
| EX-012 | `prune-cutover`; `prune.3` | Supply generic/agent-authored/old approval or mismatched digest. | No filesystem effect; exact user approval reference required. |
| EX-013 | `prune-manifest`; `freeze.3`, `prune.1` | Archive ref missing remotely, wrong commit, conflicting name or missing required external source object. | Approval packet is blocked; no force overwrite or false recovery claim. |
| EX-014 | `prune-manifest`; `prune.1` | Recover frozen source in a separate checkout using archive identities. | Resolved commit/blob hashes match; user's current tree/state untouched. |
| EX-015 | `prune-cutover`; `prune.3` | Promotion destination has unrelated changes or case/path collision. | Preflight refusal preserves both sources and destination bytes. |
| EX-016 | `prune-cutover`; `prune.3` | Interrupt staging/removal then restart recovery. | Journal exposes exact partial state; recovery restores only task-owned content and reports unresolved debt. |
| EX-017 | `prune-cutover`; `prune.3` | Dirty tracked/untracked work and old personal state exist. | No overlap is included or deleted; isolated cutover and state preservation proven by hashes. |
| EX-018 | `prune-cutover`, wave closures; `prune.3`, `eval.3` | Exceed source/module/dependency budgets or hide production code as assets/extras. | Counts explain overage and block closure; final wheel and package-data inventory are authoritative inputs. |

## 10. Main-spec integration obligations

The native spec must map each EX case to a declared task variant and exact acceptance clause. Add explicit clauses where the current short requirements do not name canonical approval hashing, adapter restrictions, package metadata/RECORD auditing, source-inaccessible execution, dirty-tree cutover exclusion, recovery-journal semantics or budget counting. These details are mandatory; leaving a generic `prune.1` link without implementing their assertions does not satisfy them.

`prune-manifest` owns manifest/recovery tooling and the concrete packet; `wave-4-close` consumes already recorded results and runs only its distinct installed consumer/identity journey. `prune-cutover` remains unstartable until the exact manifest expands its touches and the new approval is recorded. `wave-5-close` reads cutover evidence instead of rerunning it. `doctor-release` reports all remaining provider/platform qualification limits. All implementation tasks stay pending during authoring.

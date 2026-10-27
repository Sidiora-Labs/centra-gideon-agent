# Coding tools, permission policy, execution, and recovery

Status: **PROPOSED normative implementation contract, 2026-10-27. Every product scenario in this chapter is UNRUN.** This chapter elaborates [the feature specification](./spec.kvx). It does not authorise extraction, deletion, command execution, external publication, or release. Names of new interfaces and fixtures below are proposed deliverables, not statements that they already exist.

## 1. Scope, provenance, and boundaries

The coding lane SHALL expose a small set of real filesystem, source-index, subprocess, and recovery operations. Every caller uses the same effect admission path: interactive CLI, headless CLI, primary model, child agent, MCP adapter, skill helper, hook, and resumed operation. A tool description, risk annotation, model instruction, or shell command prefix is never an alternative permission boundary.

The implementation SHALL adapt the following inspected source behaviour rather than replace it with an unrelated tool stack:

| Inspected source | Existing contract to preserve | Explicit extraction work |
|---|---|---|
| [native/builtin_tools.py](../../runtime/gideon/engine/agents/native/builtin_tools.py) | read/write/edit/list/glob/grep/repo-map/bash verbs; smart case; exact-once replacement; full-result references | Remove assistant-platform verbs and global ownership adapters. `_checkpoint_pre_edit` currently catches all exceptions: the lean mutation path MUST fail closed when capture fails. |
| [native/read_gate.py](../../runtime/gideon/engine/agents/native/read_gate.py) | Per-turn observations; original-content digest; partial-region refusal; retrieval-linked evidence; 3,600-second TTL; 512-path bounded index | Preserve these carried behaviours, inject workspace/session ownership, add explicit range identity, and remove the shared no-session bucket from mutation admission. |
| [native/workspace_access.py](../../runtime/gideon/engine/agents/native/workspace_access.py) | Canonical workspace containment, file snapshots, exact text changes | A resolved-path check alone does not prevent a later symlink swap. Replace unsafe check-then-open/write boundaries. |
| [native/approval.py](../../runtime/gideon/engine/agents/native/approval.py) | Session-local `approve`, `reject`, `revise` decision delivery and timeout rejection | Keep delivery separate from policy evaluation, capability creation, and user-input provenance. |
| [tool_providers/base.py](../../runtime/gideon/integrations/tool_providers/base.py) | Definitions, results, recovery hints, output truncation, untrusted read-only annotation handling | Explicit `RiskLevel` metadata is advisory. Schema and effect capabilities determine admission. |
| [security/sandbox.py](../../runtime/gideon/security/sandbox.py) | Linux namespace/macOS sandbox backends, controlled subprocess environment, no-network failure boundaries | Replace lazy global configuration/network service ownership. Record actual backend and effective capability; unsupported confinement cannot count as enforced confinement. |
| [engine/turn_checkpoints.py](../../runtime/gideon/engine/turn_checkpoints.py) | Preimage blobs, turn preview, staged rewind, durable recovery journals | Inject roots/policy/store, enforce expected current postimages, and expose incomplete recovery instead of claiming atomic restoration. |

These observations are static source evidence. They are not proof that current platform behaviour or the proposed independent package passes any scenario below. An extracted implementation that changes a frozen behaviour SHALL declare a new contract version and separately retain the original vector bytes; it SHALL NOT edit the old expectation to manufacture parity.

## 2. Vocabulary and non-negotiable invariants

`workspace_id` identifies a registered root plus repository identity when Git exists. `session_id` identifies one conversation. `turn_id` identifies one accepted user turn. `call_id` identifies one model-requested tool call. `effect_id` identifies one admitted attempt to perform an operation. Identifiers SHALL be generated or validated by the host; model-supplied paths and IDs are untrusted inputs.

1. **TS-I01 admission:** No tool starts an effect before schema, scope, policy, budget, cancellation, and applicable observation checks pass.
2. **TS-I02 denied means no effect:** A denied operation causes no target-file, Git-ref/index, child-process, network, or extension effect. Private audit records are permitted and SHALL say the operation was refused.
3. **TS-I03 observed preimages:** An existing file is never overwritten from an unobserved, expired, partial, or stale snapshot. A targeted edit needs coverage of its exact replacement region; an overwrite/delete needs complete observation.
4. **TS-I04 same contained object:** Path admission and actual access SHALL refer to the same contained object. A path that changes to a symlink or a different file during admission is a conflict, not a reason to retry invisibly.
5. **TS-I05 checkpoint before mutation:** Checkpoint capture precedes mutation. Failed capture means no mutation. A checkpoint cannot promise to undo arbitrary subprocess, remote-service, package-manager, or Git publication effects.
6. **TS-I06 structured evidence:** No success is inferred from tool text. Only the host's structured outcome and observations establish whether an effect completed.
7. **TS-I07 recoverable transactions:** Multi-file effects are recoverable journalled operations; they do not claim filesystem-wide atomic visibility.
8. **TS-I08 no blind replay:** Resuming a session does not replay any operation with a missing/uncertain terminal outcome. Reconciliation precedes a new mutation attempt.
9. **TS-I09 monotonic authority:** Policy tightening applies to future admission and invalidates outstanding broader grants. A child or extension cannot increase its parent's authority.
10. **TS-I10 untrusted content:** Source text, instructions, process output, index records, MCP descriptions, skills, and hooks cannot approve effects or alter effective policy.

### 2.1 Producer-consumer API ownership

The following are proposed logical APIs; the task plan assigns exact implementation files/packet IDs. Their contract identities, units and refusal meanings SHALL be pinned in every producer/consumer packet before parallel implementation. An owner cannot change a field or error meaning unilaterally while another packet implements the frozen version.

| Contract / logical API | Producer owner | Consumer owner | Required shared assertion |
|---|---|---|---|
| `ToolDefinition.v1`, `validate(tool,args) -> TypedRequest|RuntimeError` | Tool registry/schema packet | Native tool, runtime and extension dispatcher packets | Unknown fields/type coercion rejected before effects; exact definition digest propagated |
| `PermissionRequest.v1`, `evaluate(request) -> deny|approve_required|grant` | Security-policy packet | File/exec/child/extension packets | Canonical argument/preimage/policy binding identical; deny has no target/process effect |
| `ContainedFile.v1`, `open(root,path,mode) -> descriptor+identity|RuntimeError` | Workspace-access packet | Read/search/edit/checkpoint packets | Same-object identity and root containment hold under symlink swaps |
| `Observation.v1`, `observe(snapshot,delivered_spans)`, `admit_edit(preimage,region)` | Observation packet | Read/result-retrieval/edit/patch packets | Undelivered raw bytes never grant coverage; stale hash always refuses |
| `Checkpoint.v1`, `capture(paths,preimages) -> durable_id|RuntimeError` | Recovery-store packet | File/patch/rewind/integration packets | Failed capture refuses before mutation; absent/existing preimages distinguished |
| `Transaction.v1`, `prepare(plan)`, `commit(id)`, `recover(id)` | Recovery-transaction packet | Patch/rewind/integration packets | Current postimage conflicts preserve external bytes; partial results explicit |
| `ExecutionReceipt.v1`, `launch(request)`, `get(handle)`, `cancel(handle)` | Process packet | Shell/repair/child/MCP/hook packets | Same actual exit/signal/timeout/process ownership and no unknown-effect replay |
| `ToolResult.v1`, `RuntimeError.v1`, `EventEnvelope.v1` | Runtime-contract packet | Every tool, CLI, provider/context/session and child collector | Four terminal statuses only, lower-snake-case error codes, typed content, session-contiguous event sequence |

`RuntimeError.v1` contains `code`, `message`, `retryable`, `recovery_hints`, bounded `details`, and optional `cause_id`; adapters SHALL preserve those semantics. Cancellation before execution uses `status=cancelled` plus `error.code=cancelled_before_run`; it is not a fifth ToolResult status. Each packet's declared verify command consumes its relevant contract fixture once. One distinct merged-wave consumer assertion covers the interaction; this table does not add duplicate task gates.

## 3. Common request, result, and event contracts

### 3.1 Tool schemas

Each exposed tool SHALL have a versioned input JSON schema with `additionalProperties=false`, required properties, length/range constraints, description, effect capability set, provider identity, and definition digest. Arguments SHALL be parsed once into a typed request; booleans SHALL NOT be accepted as integers, strings SHALL NOT be coerced to paths or timeouts, and unknown fields SHALL be rejected. Preflight validation is pure apart from bounded authorised metadata reads; it cannot execute a command, create a file, or request approval.

The host injects `session_id`, `turn_id`, `workspace_id`, policy revision, parent/child assignment identity, cancellation token, and output budget. The model cannot override them in tool arguments. Exposing one schema under several aliases SHALL preserve a single canonical operation identity and admission path.

### 3.2 ToolResult v1

The common result is:

```json
{
  "call_id": "host-validated-call-id",
  "effect_id": "host-created-effect-id-or-null-before-admission",
  "status": "completed|failed|cancelled|outcome_unknown",
  "content": {},
  "raw_ref": null,
  "truncated": false,
  "error": null
}
```

`content` is a tool-specific typed object, not an unparsed success sentence. `error`, when present, contains `code`, human-readable `message`, `retryable`, `recovery_hints`, and bounded `details`. A denied operation is `failed` with `permission_denied`; it is not `cancelled`, a successful empty result, or an exception lost in the provider stream. `outcome_unknown` means that an effect may have happened and the host cannot establish its final state; `retryable` SHALL be false until reconciliation. A command with nonzero exit returns `failed` plus the actual execution receipt. A successful grep with no matches returns `completed` with an empty match list.

Output projection SHALL preserve original byte length, displayed byte length, selector, omitted region information, and the integrity-addressed `raw_ref` when retrievable output exists. A truncated result without durable retrieval SHALL explicitly say `raw_unavailable` and why. It SHALL NOT fabricate a retrieval reference. Text previews SHALL be bounded by UTF-8 bytes, with code points kept intact; line numbers refer to original content rather than preview line positions.

### 3.3 EventEnvelope and effect lifecycle

All operational records use the shared envelope:

```json
{
  "schema_version": 1,
  "event_id": "unique-id",
  "session_id": "session-id",
  "seq": 42,
  "turn_id": "turn-id-or-null",
  "type": "tool.effect.started",
  "occurred_at": "UTC timestamp",
  "payload": {}
}
```

`seq` is contiguous in the durable session stream; concurrent tool writers obtain sequence numbers through the session writer. Output chunks and domain objects reference the same `effect_id`. The domain lifecycle is `intent → started → completed|failed|cancelled|outcome_unknown`. Permission rejection is recorded after intent and before start; no synthetic started event is emitted. Duplicate delivery of an existing event does not create another effect or consume another sequence number.

An intent includes canonical tool/version, canonical argument digest, requested scope, effective policy digest, source/working-tree identity, and applicable observation/checkpoint references. A started event SHALL become durable before dispatching an external process or applying a mutation; journal flush precedes filesystem commit. A terminal event is durable before reporting completion. If terminal persistence fails after an effect, reconciliation SHALL observe the effect and record recovery; it SHALL NOT report a clean failed/no-effect result and rerun it.

### 3.4 Error taxonomy

| Code | Meaning | Required recovery |
|---|---|---|
| `invalid_arguments` | Schema, bounds, selector, encoding, or path syntax invalid | Correct request; no approval prompt |
| `path_outside_scope` | Object is outside the admitted workspace/root set | Stop or request explicit scope change through host UI |
| `sensitive_path` | Secret, session, policy, checkpoint, or protected metadata path | No disclosure; reference policy reason without echoing secrets |
| `path_race` | Identity/ancestor changed during contained access | Reinspect the path; no invisible retry of mutation |
| `not_found` / `not_regular_file` | Required object missing or wrong type | Locate correct object or use an explicit creation operation |
| `binary_file` / `invalid_utf8` | Text operation cannot represent original bytes safely | Use an explicitly supported byte operation or report unsupported |
| `not_observed` / `expired_observation` | No current-turn usable read | Read through the authorised tool again |
| `partial_observation` / `region_not_observed` | Overwrite or region lacks observed evidence | Retrieve original result range or read required content |
| `stale_preimage` | Current bytes differ from admitted observation/preimage | Reread and recompute; never broaden match automatically |
| `ambiguous_edit` / `edit_not_found` | Replacement appears more than once or not at all | Supply exact surrounding context |
| `checkpoint_failed` | Durable recoverable preimage cannot be established | Repair state capacity/permissions; no file change |
| `permission_denied` / `approval_required` | Policy refuses or unattended approval unavailable | Host-owned decision or narrower request |
| `policy_changed` / `approval_expired` | Binding no longer current | Reevaluate complete operation |
| `sandbox_unavailable` | Required OS capability cannot be established | Report blocked; explicit policy choice required for reduced isolation |
| `budget_exhausted` / `output_limit` | Admission/duration/output/storage budget hit | Stop or request authorised budget change; retain receipt |
| `tool_timeout` / `command_failed` | Actual command timed out or failed | Return exit/signal/cleanup evidence; retry only as a new authorised attempt |
| `recovery_required` | Partial transaction or unresolved owned process remains | Fence conflicting writes and reconcile journal/process |
| `result_not_found` / `result_expired` | Raw-result reference missing/retention ended | Report loss; do not substitute a new file read as the old snapshot |
| `unsupported_operation` | Grammar, platform, patch mode, or tool version unavailable | Explicit supported fallback, never false empty success |

Errors SHALL distinguish no-effect refusal from failure after partial effects using `effect_started`, `changed_paths`, `journal_ref`, and `cleanup_status` details where relevant. OS errors are mapped without suppressing errno/class; absolute private-state paths and credentials SHALL be redacted.

## 4. Workspace identity, containment, and file objects

### 4.1 Root registration

At session start, the host records the requested root, canonical root, root filesystem identity, repository common directory when applicable, initial Git head, and a dirty-tree manifest. The requested root remains a display value; admission uses the registered canonical root. Symlinked repository entry directories are resolved once and shown to the user. A root replacement or detached mount after registration invalidates ongoing admission.

Paths in tool arguments are UTF-8 workspace-relative strings with `/` separators. `.` is accepted only by directory/search/map operations. Absolute paths, NUL, empty file paths, parent traversal, drive-qualified paths, UNC paths, and provider URI schemes are rejected in v1. Explicit extra roots are host-registered separate scopes, not path syntax escapes. Spaces, quotes, Unicode, and leading dashes are supported as literal filename components and never interpolated into shell commands.

Search defaults exclude `.git`, dependency caches, configured ignored/generated directories, and private agent state. Explicitly reading a permitted ignored source file is allowed; ignoring is search selection, not permission. A sensitive path remains blocked even when explicitly named. Search receipts state ignored-file policy digest and counts by skip reason; permission-denied files cannot silently disappear as a claim that a repository was completely searched.

### 4.2 Symlinks, hardlinks, and race boundary

V1 file mutation SHALL refuse a symlink target or any symlink ancestor below the registered root. Directory traversal SHALL not follow symlink directories. File reads may follow an explicitly named symlink only if each resolution remains within admitted roots and the final object is opened through a race-resistant contained operation; the result records requested and resolved relative identities. If the platform cannot establish that operation, symlink reads are refused rather than performed with a resolve-then-open check.

On supported systems, use descriptor-relative traversal, no-follow component opens, final `fstat`, and same-directory replace operations. A purely lexical prefix check, string `startswith`, or an earlier `Path.resolve()` is insufficient. Revalidate parent descriptor identity and final preimage under the task lock immediately before mutation. Device/inode/mount identities are local observations, never portable session identities or a substitute for the content digest.

Only regular files are text-readable/editable. Devices, FIFO/socket paths, proc-style pseudo-files, and directories SHALL be refused before a potentially blocking read. Hardlinked files with link count greater than one SHALL be refused for mutation in v1, because an external link can escape the workspace effect boundary. A future break-link operation needs a separate explicit contract. Read results expose a link-count warning without claiming containment of other links.

### 4.3 Content identity

SHA-256 is over original file bytes, before decoding, line numbering, redaction, or output projection. Size, executable bit, newline style, BOM presence, and content digest form the relevant file state; no wall-clock mtime alone proves freshness. Strict UTF-8 is the lean text format; invalid UTF-8 is refused without replacement-decoding mutation. Existing source replacement-decoding vectors SHALL remain carried historical material; any compatibility adapter is version-labelled and cannot qualify lossless editing.

CRLF/LF and missing final newline SHALL be preserved unless the requested replacement explicitly changes them. Line numbering uses logical newline boundaries: `a\nb\n` has two content lines; an empty file has zero; `a` has one. A bare CR is content, not an invented newline. NUL-containing files are classified binary. Files changing during snapshot/hash collection yield `path_race` or an explicitly inconsistent-read failure, never a digest asserted for unrelated displayed bytes.

## 5. Read and navigation tool contracts

All defaults and ceilings in this section are proposed v1 values. Effective limits SHALL be included in startup/configuration evidence. Raising a ceiling is an explicit configuration change; silently clamping an invalid request is forbidden.

### 5.1 `read_file`

Input: `path: string` required; `line_start: integer >=1` optional default 1; `line_end: integer >=line_start` optional; `max_bytes: integer 1..1048576` optional default 65536. `line_end` is inclusive. A request beyond EOF returns `completed` with no selected lines and actual `total_lines`; it does not assert the file is empty. A too-large file may be scanned/hash-streamed under a separate 32 MiB snapshot ceiling; exceeding that ceiling returns `output_limit` and asks for an explicit supported large-file route.

Output content SHALL contain requested path, resolved relative path, original byte digest, total bytes/lines, selected line start/end, displayed text, observation ID, observation completeness, encoding, newline summary, and omitted-range information. The default model preview is at most 16 KiB; full authorised snapshot output may be stored separately up to the snapshot ceiling. If the byte limit cuts a selected line, the displayed fragment is marked partial and cannot establish complete-line coverage.

The host SHALL record only content actually delivered to the model as observed, with exact original byte/line intervals. Computing a full-file hash or storing an undisplayed snapshot does not mean the model observed it. A complete observation requires every original content byte to have been delivered in ordered/nonoverlapping or safely merged pieces for the same digest, including end-of-file state. Preview labels, generated line-number prefixes, and truncation notices do not become source bytes.

### 5.2 `tool_result_get`

Input: `result_id: string` required; exactly zero or one selector family: `grep: string`, inclusive `line_start/line_end`, or half-open Unicode character `start/end`. `max_chars` optional 1..65536 default 16384. Each response additionally has a hard 32 KiB UTF-8 byte ceiling, regardless of requested character count; multibyte text is cut only at a code-point boundary with continuation/cutoff metadata. Unspecified endpoints use beginning/end. Unlike the old implicit priority among mixed selector families, lean v1 SHALL reject mixed selectors; frozen legacy behaviour remains explicitly versioned.

Only results owned by the current session/authorised child lineage are retrievable. An ID is opaque, not a filesystem path or bearer authority. Output names the original result digest, selector, returned span, original length, continuation cursor if any, and truncation state. A grep selector uses literal substring semantics with line numbers; regex matching is a separately declared future selector, not an implicit interpretation.

Retrieving a file snapshot extends observation coverage for that exact snapshot/digest. It does not reread current file bytes, reset a stale preimage to current, or grant access after policy revocation. Revoked sensitive content cannot be retrieved through an old raw result. Session retention/compaction SHALL retain required raw-result references or explicitly record their expiration; it SHALL never return newer content under an old ID.

### 5.3 `list_dir`

Input: `path: string` optional default `.`; `max_entries: integer 1..1000` optional default 200; `cursor: opaque string` optional. Output is a deterministic Unicode-code-point ordering of literal entry names with type (`file`, `directory`, `symlink`, `other`), relative path, safe size where available, and pagination. Metadata errors appear per entry. Directory mutation during pagination invalidates the cursor or labels a non-snapshot listing; it cannot silently claim complete stable enumeration.

A symlink entry is described without following its destination or leaking an out-of-root target. Permissions are checked before entry metadata reads. Hidden ordinary files are visible unless excluded by explicit policy; private agent/Git state is protected independently.

### 5.4 `glob`

Input: `pattern: string` required; `ignore_case: boolean` optional; `max_results: integer 1..10000` optional default 1000; `cursor` optional. Pattern is a relative path glob: `*`, `?`, character classes and `**` are supported; braces, shell expansions, command substitutions and `~` expansion are not. A leading `!` is a literal name, not negation. Invalid patterns return `invalid_arguments`.

Smart case SHALL preserve the existing rule: an uppercase letter in the pattern makes matching case sensitive; otherwise matching is case insensitive, unless `ignore_case` explicitly overrides it. Matching does not depend on whether the host filesystem is case sensitive. Unicode case semantics SHALL be pinned and included in fixtures. Results are workspace-relative regular-file paths, deterministic sorted order, with ignore policy, visited counts, truncation, and continuation state. Directories/symlinks are returned only through an explicitly requested schema extension.

### 5.5 `grep`

Input: `query: nonempty string` required; `glob: string` optional default `**/*`; `regex: boolean` default false; `ignore_case: boolean` optional; `max_results: integer 1..10000` default 200; `context_lines: integer 0..20` default 0. Substring and regex modes are distinct. A match is one source line, even if multiple spans occur; output includes every span on that line within declared output bounds.

Query and glob independently use smart case unless overridden by `ignore_case`, preserving the original interaction. For example, query `widget` and glob `**/HTTP*.py` means a case-insensitive text query with a case-sensitive path filter. Literal mode treats `.`/`$`/brackets as text. Regex mode SHALL use a pinned grammar with a bounded CPU/wall-time implementation; catastrophic expressions cannot block the runtime indefinitely. If the selected engine cannot enforce a per-search deadline, regex SHALL run in an owned killable subprocess or be reported unsupported. User regex is never interpolated into shell text.

Output includes query mode, matched relative path, one-based line number, original line text, code-point column spans, context lines, file digest for each loaded snapshot, scan count, skipped counts/reasons, cutoff reason, and raw reference. A displayed matching fragment may establish partial observation of those exact bytes only when linked to a complete original-file digest. Search output alone cannot establish full-file overwrite authority. An invalid/unreadable file increments an explicit skip/error count; `0 matches` is not evidence of exhaustive search when skips/cutoffs occurred.

### 5.6 `repo_map` and CodeGraph queries

`repo_map` input: optional `path` default `.`, `max_files` 1..1000 default 100, `max_symbols_per_file` 1..200 default 40. Output contains a directory outline and selected top-level definition records: qualified name where known, kind, path, start/end lines, language, file digest, parser/version, cache freshness, and parse errors. It does not claim to be an exhaustive dependency map. Ordering is stable by path then source position. Truncation is labelled at file and symbol levels.

The existing CodeGraph consumer SHALL be rehomed with explicit modes `outline`, `definitions`, and `references`, either one versioned `code_map` schema with a `mode` discriminant or separate versioned wrappers over the same implementation. The implementation task SHALL select one schema and register it once; it SHALL NOT expose two competing indexes. Definition/reference requests identify a file/position or an exact qualified symbol identity plus language; a bare ambiguous name returns candidates rather than silently choosing one.

Reference results distinguish syntactic occurrences from resolver-proven references, include declaration inclusion policy, and label unresolved/dynamic edges. Parser absence/unsupported language returns `unsupported_operation` with a text-search suggestion. A valid source file with no definitions is a successful empty result; parser failure is not. Cache keys include root identity, relative path, raw digest, grammar/configuration revision, and tool contract version. Deleted files invalidate cached facts. Graph results do not grant read evidence for undisplayed source or authorise a refactor. A stale cache may be shown only with explicit freshness labels and SHALL NOT drive mutation without fresh file reads.

## 6. Observed-content gate

The session records observations keyed by `(session_id, turn_id, workspace_id, canonical relative path, raw digest)`. No empty session or turn identifier is admitted for mutation. The default observation TTL is 3,600 monotonic seconds and the default bound is 512 paths per session, preserving the current carried contract. Eviction, turn transition, policy revocation, compaction, and session restart SHALL not magically renew authority. Restarted conversations reread before new mutation unless a future explicit persisted-observation protocol is separately qualified.

For a targeted edit, the current full-byte digest SHALL match the observed digest; `old_str` and its entire proposed occurrence region SHALL be included within delivered content from that snapshot. For overwrite or delete, complete observation is required. Multiple range retrievals may establish complete coverage only if all refer to the same immutable snapshot and include all bytes. Two matching disjoint fragments do not cover the unseen gap. Merely searching for a replacement term cannot authorise overwriting unrelated content.

After an agent edit completes, observations may be transformed only for the exact known substitution and resulting digest; a multi-file transaction updates observations after its committed receipt, never while partially applying. Any external modification invalidates the old digest even if size and mtime are unchanged. A failed write, denied edit, or preview does not create new observation authority. Read authority, permission authority, and mutation evidence are separate checks: approval does not waive missing evidence.

For new files, absence SHALL be checked and bound to the containing directory at commit time. `write_file` may create only if the path remains absent. A file appearing after preflight yields `stale_preimage`; the host SHALL NOT switch from creation to overwrite under the same approval. Creation has an absent preimage checkpoint and a real postimage digest.

## 7. Single-file mutation contracts

### 7.1 `write_file`

Input: `path: string`, `content: string` required; `expected_sha256: lowercase hex digest` optional for compatibility but injected/bound from the host observation for existing files; `create_parents: boolean` default false. Content has a v1 ceiling of 1 MiB UTF-8. Empty content is explicit and valid. `null`, a missing `content`, implicit stringification, directories, special files, and symlinks are rejected. A parent-file collision is `not_regular_file`/`invalid_arguments`, not an instruction to remove it.

Existing-file write requires complete current-turn evidence and checkpoints original bytes/mode before staging. New-file write requires absence and a checkpoint for all agent-created parent directories. Parents are created only when `create_parents=true` and within authorised scope; failure removes only empty directories created by that effect, never existing directories. Existing executable mode is preserved. New files use the user's restrictive umask and never gain executable bits by default.

Commit uses a same-directory securely created staged file, bounded write, flush, digest verification, and atomic per-file replacement after final preimage/path validation. The tool SHALL not truncate a target in place. Durability promises SHALL name the tested filesystem/profile; unsupported directory-flush behaviour is reported, not represented as crash-proof durability.

### 7.2 `edit_file`

Input: `path`, `old_str`, `new_str` required strings; `replace_all: boolean` default false; optional `expected_sha256`. `old_str` is nonempty; `old_str==new_str` returns a no-change error with no mutation/checkpoint side effects beyond intent auditing. Matching is exact Unicode text with original newline bytes; no whitespace normalisation, fuzzy matching, occurrence guessing, automatic append, or alternative-file search occurs.

With `replace_all=false`, occurrence count MUST equal one; zero is `edit_not_found`, more than one is `ambiguous_edit`. `replace_all=true` is a distinct approved request and requires observation coverage for every replaced occurrence, not only the first match. Replacement is non-overlapping, left-to-right, equivalent to the carried exact-text semantics. The host calculates the new bytes in memory/staging before commit, then uses the same checkpoint and per-file commit path as `write_file`.

### 7.3 Mutation output

Completed content includes requested/resolved relative path, operation, preimage kind/digest, postimage digest, bytes before/after, replacement count where relevant, checkpoint ID, transaction/effect ID, concise actual unified diff, and raw diff reference when truncated. A no-op SHALL be labelled no-op and SHALL NOT satisfy a task requiring changed behaviour. A failed effect includes known changed paths, checkpoint/journal reference and recovery state. Neither tool automatically formats code, runs diagnostics, tests, commits, pushes, or edits neighbouring files; the consumer loop makes those separate authorised calls.

## 8. `apply_patch` and multi-file transaction protocol

### 8.1 Wire format and limits

Lean v1 SHALL support a documented versioned patch payload. The proposed input is `patch: string` plus `preimages: array<{path,kind: absent|file,sha256?:digest}>`, maximum 100 distinct paths, 2 MiB patch bytes, and 8 MiB total staged postimage bytes. The parser recognises create/update/delete records and exact-context hunks; rename/move, binary patches, mode changes, submodule edits, symlink edits, `.git` edits, and path aliases are unsupported in v1. A rename is an explicit create+delete transaction with both observed identities, not a special guessed operation.

Every target appears exactly once in `preimages` and exactly once in the operation set, after canonicalisation and case-collision checks. Duplicate targets, create+update of one path, paths differing only by an alias on the selected filesystem, malformed hunks, unsupported headers, trailing non-patch bytes, absolute paths, or a mismatch between manifest and payload cause preflight rejection before any file change. The exact textual delimiters and escaping grammar SHALL be frozen in the owning task's schema fixtures before implementation; tool help SHALL show that grammar, not claim generic compatibility with every diff format.

Creates require expected absence. Updates/deletes require a current raw digest and the relevant read evidence. Delete requires complete observation. Each hunk's removed/context text must match exactly at the declared unique position; no offset fuzz, automatic whitespace cleanup, or searching another file. The parser may accept a relocated unique hunk only in a future explicitly named compatibility version; lean v1 rejects mismatched line/context assertions.

### 8.2 State machine and crash windows

Transaction states are `PREPARED`, `CHECKPOINTED`, `STAGED`, `COMMITTING`, `COMMITTED`, `ROLLING_BACK`, `ROLLED_BACK`, and `RECOVERY_REQUIRED`. These states live inside the shared effect lifecycle; `COMMITTED` enables a completed effect, `ROLLED_BACK` produces a failed effect with `changed_paths=[]` and restoration proof, and `RECOVERY_REQUIRED` produces `outcome_unknown` with an explicit blocking journal.

1. Parse and validate the entire patch without side effects. Derive exact capability/path set and request permission once for that set.
2. Acquire in-process workspace mutation lock and sorted per-path locks with bounded timeout. Locks prevent cooperating agents racing; they do not substitute for external-change detection. Intersecting transactions serialize; disjoint reads may proceed.
3. Open targets and parent directories through contained descriptors, validate identities/current hashes/observation coverage, and reject same-object aliases.
4. Capture all preimages including absent-file state and modes into the private checkpoint store. Verify blob digests and durable manifest before proceeding. Out-of-space or secret-exclusion capture failure blocks the entire transaction.
5. Stage every postimage in the corresponding target filesystem; validate sizes/digests; write and flush a journal containing pre/postimage identities, stage ownership, commit order, policy digest, and per-path progress. Staged filenames are unguessable and exclusive; never reuse user-created files with a convenient suffix.
6. Revalidate policy, cancellation, path identities, expected current preimages, and approval binding. Transition to `COMMITTING`. Commit in deterministic path order, recording each successful replacement/delete durably. Observers may see intermediate files: consumers SHALL not call this an atomic multi-file snapshot.
7. After every path matches its postimage and metadata, flush the committed manifest, emit completed receipt, then release locks and remove owned staging artifacts. Cancellation after commit yields a committed effect followed by turn cancellation; it does not lie that the committed change never happened.
8. On failure after any committed path, enter `ROLLING_BACK`. Restore only paths still matching this effect's known postimage or checkpointed absent state. An external modification, unreadable state, missing blob, or failed compensation yields `RECOVERY_REQUIRED`; never overwrite it to finish a rollback.

Restart scans unfinished journals before new mutation. For each path, observed state is classified as preimage, postimage, or external/unknown. The host may finish a recorded rollback using those exact identities; it SHALL not replay arbitrary patch interpretation/model requests. A partially committed transaction is not reported as successful because all stage files exist. Recovery decisions and discarded owned stage artifacts are audited. Deleting a journal without reconciling its paths is forbidden.

### 8.3 Patch result

Content includes transaction state, ordered path outcomes, actual combined diff, checkpoint ID, pre/postimage manifest digests, committed/restored/conflicted paths, journal reference if unresolved, and known durability profile. Failure before commit states `no_target_changes=true`. Failure with a verified rollback states `restored=true`; rollback attempted but unverified states neither. The CLI SHALL surface recovery-required distinctly and fence new edits to affected roots until resolved.

## 9. Checkpoints, diff, and rewind

Each turn checkpoint records the first preimage and every owned mutation's postimage chain. Preexisting tracked and untracked bytes are equally protected. Git clean status is not a prerequisite to coding and SHALL never be manufactured through reset/clean/stash operations. Checkpoint storage is session-private, outside the worktree, digest-addressed, versioned, and excluded from model filesystem tools.

Checkpoint size/count retention is explicit. A required preimage is never silently omitted because of a size cap or sensitive-file rule. The associated protected mutation is always refused in v1; there is no non-reversible-checkpoint override mode. Garbage collection retains blobs referenced by active sessions, unfinished journals, queued rewind previews, or unresolved child submissions. It reports capacity debt rather than deleting live recovery material.

`/diff` and a proposed `checkpoint_diff` internal operation report the actual current delta owned by a selected turn, with external differences labelled. A model-generated patch preview is not the actual diff. `rewind_preview(checkpoint_id)` is read-only and produces path actions (`restore`, `delete_agent_created`, `unchanged`, `conflict`, `unsupported`) plus current digests and a preview digest. `rewind_apply(preview_id)` is a separate mutating effect under the same policy, observation, checkpoint and transaction rules.

Apply requires that every path still equals the preview's expected current identity and the latest agent-owned postimage. An external change after the preview causes conflict refusal. V1 rewind is all-or-refuse at preflight for any conflicting selected path, then journalled compensation if commit fails; it SHALL not restore safe-looking paths and call the whole rewind successful. The user may explicitly create a narrower preview for nonconflicting paths. A rewind also creates a safety checkpoint so an authorised forward restoration can be proposed later.

Rewind SHALL not reset Git refs/index, remove unrelated untracked files, undo package downloads, retract network requests, kill arbitrary processes, or restore deleted remote resources. It reports these known non-file effects from execution receipts as non-reversible. Shell-created filesystem changes are not claimed turn-owned unless an explicit execution snapshot/diff mechanism identified their preimages and postimages; otherwise rewind says unsupported. Conversation history remains append-only with a rewind event; it is not rewritten to pretend the original effect never happened.

## 10. Unified permission policy

### 10.1 Capabilities and modes

Capabilities are explicit sets, including `workspace.read`, `workspace.write`, `workspace.delete`, `process.exec`, `process.background`, `network.connect`, `git.metadata.read`, `git.metadata.write`, `git.publish`, `child.spawn`, `extension.launch`, and `external.effect`. Path roots, executable/argument bounds, destinations, environment references, time/budget bounds, and actor identity constrain those sets. `SAFE`/`CAUTION`/`DESTRUCTIVE` labels assist display; they never grant capabilities.

| Mode | Default permitted operations | Missing permission handling |
|---|---|---|
| `read-only` | In-root non-sensitive native reads/search/maps and private session/audit maintenance | All workspace mutation and arbitrary execution refused. An apparently read-only shell command still requires execution and cannot bypass this mode. |
| `ask` | Same reads plus effects covered by active scoped user grants | Trusted CLI asks for the exact proposed operation; headless/unattended invocation returns `approval_required` unless a matching prior grant exists. |
| `unrestricted` | Effects within the explicit invocation's selected roots/capability envelope | No repetitive prompt for covered effects; sensitive host state, parent policy ceilings, version checks, observation gates, and applicable hard denials still apply. OS confinement remains a separate declared capability. |

Mode selection is host-controlled. Repository configuration may request a narrower mode or list needed capabilities; it SHALL NOT select unrestricted mode, enable hooks, add roots/network destinations, or import credentials silently. User configuration is loaded from a private explicit location. `--unrestricted` is an explicit user-facing invocation choice, recorded with scope; an agent cannot add that flag to its own child command to escape policy.

### 10.2 Rule evaluation and grants

Evaluation order is deterministic: malformed request → cancelled/expired actor → host hard exclusions → inherited parent ceiling → read-only ceiling → explicit deny rules → scope/destination constraints → exact active operation grant → persistent allow rule → mode default → approval request/refusal. A specific allow cannot override a deny. Policy has a revision/digest and origin provenance; unknown rule syntax is an error, not an ignored entry.

An approval request contains request/effect ID, tool/version, actor/parent, exact canonical arguments or redacted secret references, path list, actual diff or executable/argv/command, cwd, network destinations/capability, requested environment names, sandbox profile, expiry, and one-time/persistent scope choice. Approval binds the digest of that packet plus policy revision and expected preimages. Changing command, path, diff, server definition, cwd, environment, or network scope invalidates it. `revise` returns to the model for a new proposal; it does not mutate an already-approved command in place.

Only authenticated local terminal/control input resolves approval. Stdout, provider deltas, clipboard contents pasted as tool output, MCP `approve=true`, and a skill's prose cannot resolve a request. Timed-out/disconnected/cancelled requests reject. A request delivered twice consumes at most one decision; answers for another session or expired request are refused. A rejected operation SHALL not be retried under a tool alias, a different shell spelling, a child agent, or MCP.

Persistent rules are user-owned, private, revisioned, and revocable. Root-scoped path rules use canonical root identity and relative-component patterns; moving the repository does not silently inherit a broad rule. Command rules use structured executable identity/argv constraints for shell-free calls. Shell-text prefix approval is insufficient for pipelines, redirects, substitutions, functions, nested shells, `env`, interpreters, executable scripts, or argument expansion. A complex shell request binds its full string and confinement profile; new semantics need a new decision.

### 10.3 Process and network separation

`process.exec` does not imply network, credential access, publication, or unrestricted host filesystem access. Network deny SHALL be enforced by the actual OS process boundary when required. Destination allowlists require a qualified egress mechanism; DNS checks or a proxy environment variable alone do not establish a destination fence. If v1 supports only full network-off or explicitly full-network-on, it SHALL report those two actual capabilities rather than claiming unsupported endpoint granularity.

The provider process may connect to its configured inference endpoint under the user's provider policy. Repository tests and child shell processes SHALL not inherit that provider credential or outbound authority. Inference environment references are resolved in the provider owner and redacted in logs. A simple environment filter alone does not prove credential secrecy from same-UID processes; required profiles SHALL also hide credential files/process environments or use a stronger process boundary. If that cannot be enforced, the CLI reports the exposure and blocks profiles requiring secrecy; it does not claim isolation from separate directories.

## 11. OS confinement and unsupported platforms

Backend selection SHALL report OS/architecture, backend binary/version where available, capability probe identity, effective filesystem read/write scopes, network mode, process limits, secret exclusions, and known limitations. A working executable or successful probe alone is insufficient: negative probes SHALL attempt an outside-root write/read of a designated protected canary, forbidden network connection, and forbidden private-state read under the selected profile. Probes use disposable non-secret canaries and are owned by qualification, not every task invocation.

Linux namespace and macOS sandbox implementations may be adapted only after severing global platform dependencies. Windows or any other unqualified backend returns `sandbox_unavailable` for required confinement; text reads may still operate through contained filesystem tools where qualified. The product SHALL document supported operations per profile instead of claiming universal sandboxing.

Required confinement defaults fail closed. An explicit reduced-isolation profile is a separate user-controlled choice, visible in startup, approval and every execution receipt. An `auto` backend that silently falls back to bare host execution SHALL not qualify. Backend loss between probe and launch causes refusal; no retry with `off` occurs automatically. A successful host command after confinement setup failure is a security failure even if it produces the expected build output.

Sandbox writable scope includes only admitted workspace paths plus owned temporary/output directories; private session/checkpoint/policy/provider homes are not generally writable or readable by repository subprocesses. Git metadata roots and linked-worktree common directories receive separate policy. Agent-owned logs are append-only through a host output channel; a test process cannot overwrite its own final receipt or a previously sealed log.

OS confinement limits damage but does not establish semantic command safety. Package scripts can execute code and need the same capability admission. Command-name deny lists and shell parsing provide useful detection/display, but SHALL NOT be presented as proof that arbitrary Python, compiler, test, or shell code cannot mutate. Dependencies, shell startup scripts, Git hooks, editor plugins, and tool configuration are executable repository input and must be confined accordingly.

## 12. Execution tools and process ownership

### 12.1 `bash` request and launch

Input: `command: nonempty string` required; `timeout: integer 1..600` default 120; `cwd: relative directory` optional default `.`; `env: object<string,string>` optional with allowlisted names; `background: boolean` default false. A future structured `exec` argv interface may coexist but SHALL use the same gate and receipts. Command text is handed as a single argument, never interpolated through another host shell. NUL and requests above 64 KiB are rejected.

Unlike the old login-shell default, lean v1 SHALL use a documented pinned shell invocation without implicit user startup-file execution. A compatibility mode for login-shell behaviour is explicit and records additional config inputs. The implementation SHALL not source arbitrary RC files merely to locate executables. Executable search path, shell binary identity, cwd identity and allowed environment digest are recorded. Environment variables cannot replace system home/runtime state owners or inject loader/shell startup behaviour outside declared policy; `LD_PRELOAD`, `DYLD_*`, `BASH_ENV`, and credential variables are excluded unless a separately scoped profile authorises them.

The host creates a new owned process group/session, assigns a random process handle and captures creation time/boot identity. Cwd is validated through contained access before launch. Environment is built from an explicit non-secret allowlist plus task-scoped references; it does not inherit the full host environment. Launch failure returns no PID as active and releases owned temporary artifacts.

The receipt records command digest plus display text with secret references redacted, cwd, workspace/tree identity before/after where observable, actor/effect, effective backend/policy, start/end UTC and monotonic duration, process identity, output references, exit code or signal, timeout/cancellation, output/storage cutoff, and cleanup status. An exit code is `null` until known; a timeout is not invented exit 0 or a guessed signal. Commands can legitimately fail; that is evidence for the repair loop, not necessarily a tool infrastructure defect.

### 12.2 Output collection

Stdout/stderr are captured as distinct streams with sequence and timestamps; the CLI may present a merged ordered view labelled by stream. Cross-stream total byte ordering is observational, not asserted as kernel causality. Default preview is 32 KiB; default retained output ceiling is 16 MiB per effect and configurable within the run budget. Output pressure SHALL drain pipes independently of UI rendering. On retention exhaustion, record dropped-byte counts/cutoff and terminate or explicitly continue under the selected policy; never block the child indefinitely on an unread pipe or claim complete logs.

Redaction runs before content enters model-visible output and normal durable artifacts, including values split across read chunks. Registered secret values, credential fields and private-state paths are redacted. Redaction is bounded and does not establish that arbitrary unknown secrets cannot appear; credential prevention and scope enforcement remain primary. Terminal control characters are escaped for rendering so command output cannot spoof approval prompts, erase status, or inject terminal commands.

### 12.3 Timeout, cancellation, and background ownership

Cancellation revokes further admissions immediately, sends termination to the owned group, waits a configured five-second grace interval, then kills owned remaining descendants. It SHALL not signal arbitrary processes based only on a reused PID. The host confirms process identity and group ownership. Descendants escaping ordinary process groups require an effective backend lifecycle boundary; if cleanup cannot be established, return `cleanup_pending`/`recovery_required` and preserve the handle. A process liveness check by itself does not prove child descendants are gone.

Background launch is a separate capability. The initial result is `completed` for successful launch only and content says `process_state=running`; it does not claim the command completed. `process_get(handle, cursor?, max_bytes?)` retrieves output/status; `process_wait(handle, timeout)` waits within the caller budget; `process_cancel(handle)` follows owned cancellation. Handles are session/actor scoped and not raw PIDs. Finished results remain retrievable subject to explicit retention.

V1 shell state is not persistent: each command starts from recorded cwd/env; `cd`/`export` in one call do not silently affect another. The process handle persists, not a magical shared shell. A future persistent PTY/shell extension needs its own state/input provenance and cancellation contract.

On host restart, unconfirmed handles become `outcome_unknown` until process identity and retained status are reconciled. The host SHALL not replay the original command to discover its result. A completed child process with a lost exit record remains unknown unless an independent durable status record proves completion. Session resume exposes unresolved effects before further conflicting writes.

## 13. Verification consumer and Git boundaries

A repair task's baseline and post-edit verification are actual execution calls. Their receipts bind command, selector, cwd, relevant environment/toolchain, source/dirty-tree manifest, and output. The task's registered command cannot be replaced by `echo passed`, a model summary, or a different zero-exit check. If a framework exposes counts, zero tests, required skips, and failed collection remain visible; absent counts are unknown. Missing credentials or sandbox capability is blocked/unqualified, not skipped-as-pass.

The default repair budget is one complete implementation followed by one declared build and one declared verify invocation, with at most two relevant repair/rerun cycles and 30 minutes per task. A command timeout consumes the budget. Baseline evidence is gathered only if authorised by the task and is distinct from qualification of the final implementation. Unchanged failure attribution requires comparable baseline/post-edit inputs and matching failure evidence; otherwise report unknown. Repeated unchanged commands are not a confidence campaign.

Git status/diff SHALL use real Git with an explicit repository directory and safe argument vector. Repository-controlled external diff, textconv, pager, fsmonitor, or hooks SHALL not execute outside the command gate. Reads specify raw versus rendered diff and binary handling. Ordinary file mutations never silently stage changes. Commit, reset, clean, checkout-overwrite, merge, worktree removal, remote mutation and push require their own admitted capabilities and task authorisation. A user asking for code changes does not automatically authorise external publication. Worktree-child custody and integration are defined in [delegation and extensions](./delegation-extensions.md).

## 14. Concrete vectors and negative acceptance scenarios

All rows below are **UNRUN proposed scenarios**. The owning task SHALL map each applicable scenario to exact fixture bytes/assertions and the single task verify command in [the feature specification](./spec.kvx). Tests use actual temporary files, Git repositories, subprocesses and OS boundary probes. Controlled fault injection surrounds real filesystem/syscall/store boundaries; it does not replace the tool implementation with a success-returning fake. A task-level component pass does not establish a later real-model or release scenario.

| ID | Fixture/action | Required observable assertions |
|---|---|---|
| TS-001 | File bytes `alpha\nbeta\ngamma\n`; read lines 2..2. | Only `beta\n` delivered as source; line=2; full raw-byte digest bound; partial observation; overwrite refused, exact observed `beta` edit eligible subject to permission. |
| TS-002 | UTF-8 file with CRLF, emoji, BOM and no final newline; replace one observed token. | Unchanged bytes remain byte-identical; original newline/BOM state preserved; columns count documented code points; digest identifies original/new bytes. |
| TS-003 | Invalid UTF-8, NUL-containing file, FIFO, socket and device. | Text operation refuses safely without replacement-decoding, blocking, or special-file read; target unchanged. |
| TS-004 | File read preview truncates between two disjoint fragments; attempt replacement spanning the omitted gap. | `region_not_observed`; stored full output is not treated as delivered evidence. Fetch exact immutable range, then targeted edit may proceed. |
| TS-005 | Read first and second halves of same immutable snapshot through result retrieval. | Combined coverage becomes complete only after all bytes/EOF delivered; overwrite then uses current matching digest. |
| TS-006 | Raw result belongs to another session or policy was tightened after original read. | Retrieval denied; no cross-session bytes or resurrected permission. Expired result returns explicit expired, not a fresh reread. |
| TS-007 | `old_str="x"` against bytes `x x\n`; default then replace-all. | Default `ambiguous_edit` leaves bytes unchanged. Replace-all requires both occurrence regions observed and a distinct binding; result records two substitutions. |
| TS-008 | Empty old string, identical old/new, missing content, `null`, boolean timeout and unknown argument. | Schema/no-change errors before mutation; no implicit create/append/coercion. |
| TS-009 | Read digest A; external editor writes digest B with same size/mtime; agent edits A. | `stale_preimage`; B survives byte-identically; no checkpoint rollback overwrites it. |
| TS-010 | Create intent for absent file; external process creates it during approval. | Revalidation rejects expected absence; existing external file survives. |
| TS-011 | Swap a parent or target for an out-of-root symlink between preflight/open/commit. | Contained operation refuses `path_race`/scope; outside canary unchanged and unread. Run each race point deterministically. |
| TS-012 | Inside file hardlinked to outside canary; mutate via inside name. | Mutation refused in v1; outside bytes unchanged. |
| TS-013 | Smart-case fixture `http.py`, `HTTP.py`, `Http.py` and mixed-case text. | Glob/query default smart case and explicit overrides match frozen expected sets independent of host path case semantics. |
| TS-014 | Literal query `a.b$` versus regex query `a.b$`; malformed and catastrophic regex. | Literal characters preserved; regex semantics explicit; malformed rejected; pathological search bounded and owned process cleaned. |
| TS-015 | Matches beyond result cap, unreadable file, ignored dependency tree and binary file. | Cutoff/skips enumerated; no false exhaustive zero/complete result; raw retrieval and continuation stay scoped. |
| TS-016 | CodeGraph parses source then source changes/deletes; grammar missing for another file. | Digest-keyed invalidation; deleted symbol absent; grammar absence explicit unsupported/fallback; no phantom empty-success index. |
| TS-017 | Disable checkpoint store write or fill its quota immediately before edit. | `checkpoint_failed`; target bytes/mode and Git state unchanged. Existing exception-swallow behaviour cannot satisfy this case. |
| TS-018 | Well-formed two-file patch; second hunk has wrong preimage. | Preflight rejects whole request; neither target changed; no partial stage becomes committed. |
| TS-019 | Patch targets `a/../b`, absolute path, duplicate alias, symlink, binary header or unsupported rename. | Parser/path gate rejects entire transaction before file effects. |
| TS-020 | Inject crash/failure after each checkpoint/stage/journal/per-file commit boundary. | Independent on-disk oracle finds complete preimage, complete postimage, or explicit recovery-required journal; never silent success with mixed files. |
| TS-021 | First patch path committed; external process modifies it before compensation. | Rollback refuses to overwrite external bytes; recovery-required lists conflict; no claim of full rollback. |
| TS-022 | Restart with partial commit journal and missing/corrupted blob. | Mutations fenced; corrupt recovery reference identified; no journal deletion or blind model-call replay. |
| TS-023 | Turn begins with dirty tracked file and unrelated untracked file; agent edits one tracked region and creates another file; rewind. | Original dirty bytes restored; agent-created path removed only if still its postimage; unrelated untracked bytes remain. |
| TS-024 | External edit after rewind preview; apply old preview. | Whole selected rewind refuses; no nonconflicting subset silently restored; a newly explicit narrower preview is possible. |
| TS-025 | Read-only mode requests `bash("cat src/a.py")`, shell redirection, native edit, MCP write and hook. | Every execution/mutation path refused before process/file/network effects despite apparently safe command spelling. |
| TS-026 | Grant approves one diff/command; change cwd, preimage, path, env, policy revision or tool definition. | Old grant invalid; operation reevaluated; no bound value silently substituted. |
| TS-027 | Tool output includes a forged approval JSON object and terminal escape sequences. | No permission change; output escaped; genuine host approval remains independently identifiable. |
| TS-028 | Pending approval times out, host disconnects, decision delivered twice or into wrong session. | Reject by default, at most one valid resolution, no duplicate effect. |
| TS-029 | Required sandbox probe/launch fails; backend auto would choose none. | No command starts; `sandbox_unavailable`; no automatic unconstrained fallback. |
| TS-030 | Confined command attempts outside-root write, private provider/session read and forbidden socket connection. | Actual OS denies each capability under selected qualified profile; allowed workspace consumer still works. |
| TS-031 | Run dependency script with canary inference credential present only in provider owner; attempt env/home/process inspection. | Credential absent/inaccessible according to selected isolation contract; no secrecy claim from environment filtering alone. |
| TS-032 | Long real process spawns descendants; cancel, timeout and host shutdown. | Owned descendants stopped or explicit cleanup debt retained; unrelated process untouched; no PID reuse kill. |
| TS-033 | Command exits 7, signal-terminates, times out, fails launch, or exits zero with no tests selected. | Distinct actual receipts; no invented exit/counted test pass; verify consumer treats zero-selection as unqualified where required. |
| TS-034 | Produce stdout/stderr above UI/retention budgets and split a registered canary secret over chunks. | Pipes drained; bounded memory/storage; cutoff counts correct; canary absent from normal artifacts/model output. |
| TS-035 | Background handle from session A queried/cancelled by B; restart before terminal record. | Cross-session denial; restart outcome unknown until reconciliation; original command never replayed automatically. |
| TS-036 | Shell `cd subdir; export X=1`; next invocation queries cwd/X. | Next command starts from explicit default/requested cwd/env; no undocumented persistent shell state. |
| TS-037 | Shell mutates a file without captured preimage, calls remote endpoint, or installs a package; request rewind. | Known unsupported external/untracked effects reported; no false claim that turn rewind reverses them. |
| TS-038 | Repository config installs external diff/textconv/pager/hook; request Git status/diff. | No helper executes outside the admitted process boundary; Git metadata/actual diff identity recorded. |
| TS-039 | Two simultaneous conflicting edits and disjoint reads. | Conflicting writes serialize or one refuses stale; read outcomes identify actual snapshots; no lost update. |
| TS-040 | Effect changes disk but terminal event persistence fails; repeat model call/resume. | Existing effect reconciles via journal/postimage; no duplicate replacement or command replay; uncertainty visible. |

## 15. Completion obligations

The tool/security slice is implemented only when its exposed schemas, CLI consumers, permission path, durable records, real adapters and required negative fixtures exist together. A class definition without a consumer is incomplete. Passing a unit test with a replaced filesystem/sandbox implementation cannot establish the OS boundary. Passing one OS profile does not qualify another. `UNRUN`, `BLOCKED`, `UNKNOWN`, unsupported, output-truncated, recovery-required, and cleanup-pending cases remain explicit in receipts and release readiness.

The specification's task graph selects the single focused build/verify gate for each implementation task and the distinct merged-wave integration journey. This chapter does not add a second verification campaign, automatic review fleet, or repeated passed gate. It supplies the detailed observable assertions those declared gates must consume.

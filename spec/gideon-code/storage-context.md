# Gideon Code durable storage, context and recovery contract

Status: **PROPOSED; implementation pending; all behavioural cases UNRUN.** This chapter defines the file-backed authority required by [runtime-contract.md](./runtime-contract.md), not an existing working store. [spec.kvx](./spec.kvx) owns task statuses, sequencing and implementation authorisation. These contracts deliberately require no database server, gateway or personal-memory graph.

## 1. Authority, scope and source grounding

| Component | Authoritative for | Must not be mistaken for |
|---|---|---|
| Session journal | Accepted conversation/events, effect intents/results, instruction/context version lineage | Proof that an arbitrary external effect happened merely because intent exists. |
| Content-addressed object files | Exact retained bytes when verified by size and SHA256 | Permission, test success, or model truth. |
| Project filesystem and Git | Current source bytes and repository objects/refs | Exclusive ownership of every change observed during a shell command. |
| Checkpoint/transaction manifests | Agent-owned before/after observations, recovery plans and their provenance | A filesystem-wide atomic transaction or backup of arbitrary personal state. |
| Derived manifest/catalog | Fast session listing and current-state projection | Independent authority when inconsistent with journal. |
| Compacted context | Bounded model request projection of durable history | Replacement history, a new user command or a permission grant. |
| User-trusted config/policy | Permitted actions and budgets at a recorded snapshot | Authority to rewrite prior evidence retroactively. |

Existing [session_workspace.py](../../runtime/gideon/engine/session_workspace.py) supplies useful session/result naming and atomic-result-writing primitives. Its current append/replay is insufficient for this design: it appends without an explicit sync/locking contract and skips malformed JSON lines. The proposed store must reject middle corruption and expose tail repair; copying that replay method unchanged does not meet acceptance.

Existing [turn_checkpoints.py](../../runtime/gideon/engine/turn_checkpoints.py) supplies preimages, bounded capture, manifests and journalled rewind. Its configuration/root discovery and secret/noncapture cases require injected lean boundaries. Existing [read_gate.py](../../runtime/gideon/engine/agents/native/read_gate.py) supplies observed-content evidence. Existing [context_compaction.py](../../runtime/gideon/cognition/context_compaction.py) supplies output pruning and protected message slices but lazily reaches `resume_account`; extraction must sever or explicitly port that dependency. These findings are static, not runtime qualification.

### 1.1 Storage invariants

S-01. The journal is the sole authority for accepted session state. A manifest lag is repairable; a manifest ahead of the validated journal is corruption, not permission to skip records.

S-02. One session has one writer under an OS-enforced exclusive lock for the lifetime of recovery and mutation. A stale-looking PID or timestamp alone never authorises stealing the lock.

S-03. Acknowledgement of an effect intent, checkpoint readiness or completed effect requires its referenced objects and journal record to be durably persisted under the qualified filesystem profile.

S-04. An incomplete tail can be quarantined and repaired. Invalid bytes in the middle, sequence gaps, wrong identity or checksum mismatch stop normal replay; never skip and continue silently.

S-05. Journal replay is pure projection. It performs no provider calls, shell commands, network effects, edits, hooks or child launches.

S-06. Recovery classifies unfinished effects before conflicting work resumes. Absence of a result record never proves the effect did not occur.

S-07. Rewind restores only proven agent-owned changes whose current postimages still match; unrelated user bytes are preserved. Git cleanliness is neither assumed nor required.

S-08. Compaction preserves original history and valid provider tool-call pairing. Summaries are reference material with explicit provenance and cannot increase authority.

S-09. Instruction snapshots are content-addressed and scope-aware. A nested instruction cannot widen roots, elevate permissions or supersede direct user authority.

S-10. Quota, redaction or capture refusal cannot be hidden behind a success marker. If the operation depends on durable evidence that cannot be retained, stop before the effect.

S-11. Personal/platform state is not migrated implicitly. Source cutover does not delete, rewrite or assume compatibility with existing `GIDEON_HOME` data.

S-12. Checksum integrity is not authentication against a malicious same-user process. State permissions and the local threat model are explicit; do not advertise cryptographic tamper proofing.

## 2. Identity and path model

Logical IDs are lowercase UUID-based opaque strings with a record-kind prefix: `ses_`, `inv_`, `trn_`, `exc_`, `eff_`, `evt_`, `ctx_`, `ins_`, `ckp_`, `txn_`, `prc_`. Canonical form is prefix plus hyphenated UUID; comparison is byte-exact. User text, model output and remote provider identifiers are never interpolated into storage paths. Provider call IDs remain bounded string attributes namespaced by `exchange_id`, not filenames.

A project identity contains `project_id`, canonical root, root filesystem identity where available, Git common-directory identity where applicable, repository origin fingerprint if present, and creation observation. No network contact is needed to compute it. Origin URL is a non-authoritative hint and is redacted if it contains userinfo. Two worktrees of one Git common directory share repository identity but retain distinct `workspace_id`/root binding. A copied directory or moved path is not silently assumed identical merely because its basename matches.

Canonical roots resolve symlinks before admission. Path identities inside records are normalised project-relative POSIX paths, plus an explicit root ID. Absolute cwd and original display path are separate metadata. Reject NUL, `..` traversal, drive/UNC escapes, path separators in opaque IDs and paths resolving outside admitted roots. Root discovery at replay does not widen roots from the current user's global config.

### 2.1 Proposed local layout

```text
$XDG_CONFIG_HOME/gideon-code/                 # ~/.config/gideon-code fallback
  config.toml                               # user-authored; credential references only
  policies/<policy-id>.toml                  # scoped trusted decisions, revocable
$XDG_STATE_HOME/gideon-code/                 # ~/.local/state/gideon-code fallback
  schema.json                               # installed store format and writer compatibility
  projects/<project-id>/project.json         # identity hints; validated against session binding
  sessions/<session-id>/
    writer.lock                             # stable inode; never unlink while locked
    manifest.json                           # replaceable cache; journal-derived
    events.jsonl                            # authoritative append-only stream
    objects/sha256/<first-2>/<full-digest>    # immutable session-owned content
    checkpoints/<checkpoint-id>.json         # immutable manifest or object reference
    transactions/<transaction-id>/
      plan.json                             # immutable intended operations/preimages
      progress.jsonl                        # durable commit/recovery progress
    projections/<context-id>.json            # immutable request projection
    quarantine/<recovery-id>/                # original torn/corrupt tail bytes and report
    logs/<logical-process-id>/               # bounded stream chunks/manifests
    tmp/                                    # private staging; never authoritative
```

An object may be physically stored by digest within its session to avoid a cross-session GC/refcount service. Global deduplication is not required in v1. Config/state roots must be distinct from workspace tool-writable roots unless the user deliberately selects otherwise and containment protects them. A repository symlink pointing at the state directory must not grant tool access.

Create directories with user-only mode (0700) and files with 0600 under a restrictive creation mask on supported Unix profiles. Verify existing root owner and reject unsafe symlinks or world/group writable state directories before opening a writer. Secret bytes are never included in manifests, paths or permission-decision keys. On unsupported ownership/locking/durability platforms, advertise `unqualified` and refuse required durable-effect mode rather than guessing equivalent behaviour.

## 3. Serialization contract

All records are UTF-8 JSON objects. Numbers used as counters are integers in `[0, 2^53-1]` for interoperable JSON consumers. Cost/large-byte counters requiring a larger range use canonical nonnegative decimal strings; no NaN, Infinity or negative zero. Duplicate JSON keys are rejected. Persisted fractional values use canonical decimal strings; binary floating-point values are not allowed in hashed journal payloads. In-memory provider sampling/deadline numbers are converted explicitly at the persistence boundary. Strings have explicit bounds; object nesting is at most 32. A physical journal line contains exactly one JSON object and a final LF; embedded newlines are escaped.

Canonical hash encoding uses UTF-8, lexicographically sorted object keys, no insignificant whitespace, Unicode emitted without optional ASCII escaping, and deterministic integer/decimal-string representation. Hashes use lowercase `sha256:<64 hex>` on exact canonical bytes. Unicode text content is preserved as bytes; never NFC-normalise file contents or prompt text while claiming the same digest. Paths may normalise separators only under the declared path schema.

The authoritative journal record extends the runtime's public minimum envelope:

```text
JournalRecord {
  schema_version: 1,
  event_id: Id, session_id: Id, seq: positive int,
  turn_id: Id|null,
  type: registered event type,
  occurred_at: RFC3339 UTC string,
  payload: bounded object,
  previous_hash: Digest|null,
  record_hash: Digest
}
```

First record has `seq:1` and `previous_hash:null`. For later records `previous_hash` equals the preceding complete record's hash. Compute `record_hash` over the canonical record excluding only `record_hash`. Readers validate hash/identity/sequence before projection. Public event output may omit hash fields, but must not invent a different event ID/sequence/payload. A local actor with write access can rewrite an entire chain; the design protects accidental damage and detects mismatched copies, not hostile local administration.

Unknown major `schema_version` stops mutation. Unknown event type or unknown mandatory payload field version stops projection; additive optional metadata may be retained and ignored under an explicit version capability rule. Never drop unknown authority/effect records and continue. A read-only raw export may preserve unsupported bytes without claiming a successful semantic resume.

### 3.1 Required record payloads

| Record | Required fields and validation |
|---|---|
| `session.created` | Session/project/workspace IDs; canonical root binding; format version; creating invocation; source/config snapshot digests; runtime version. Must be first. |
| `invocation.started` | Fresh invocation ID; effective non-secret config/policy digest; requested model; cwd; limits; resume source seq/hash. |
| `turn.started` | Turn ID; original message ID/object ref; accepted instruction sequence; context snapshot ID; task/parent constraints. No duplicate accepted turn ID. |
| `model.requested` | Exchange ID; immutable message projection/tool schema digest; adapter/model aliases; request options and known capacity; prior retry relation. |
| `model.completed` | Exchange ID; actual completion reason; assistant message/call group refs; observed usage with completeness/source. Cannot precede request. |
| `effect.intent` | Effect ID; originating exchange/call; operation kind; redacted arguments and canonical digest; exact cwd/roots; policy/decision; planned checkpoint/transaction IDs; idempotency/recovery class. |
| `effect.started` | Effect ID; launch/process identity or file transaction phase; monotonic/wall observations; no claim of outcome. |
| `effect.result` | Effect ID; observed outcome; `effects_observed:none|partial|complete|unknown`; command/file/result refs; exit/signal where known; uncertainty/recovery disposition. |
| `checkpoint.ready` | Checkpoint ID; complete manifest hash; covered root/path set; object refs; exclusions; captured preimage hashes. No effect starts on an incomplete required checkpoint. |
| `context.compacted` | Old/new projection IDs; source sequence range; source hashes; summary/object refs; protected material and token/byte accounting. |
| `turn.completed` | Terminal runtime state; task status; unresolved effect IDs; usage completeness; verification refs; final response ref. At most one per turn. |
| `recovery.disposition` | Recovery ID; original valid tail sequence/hash; exact affected effect/transaction IDs; observations, action and authenticated decision if needed. |

Text deltas may be chunked into bounded durable events. A UI may display explicitly provisional text before its sync acknowledgement, but acknowledged message/effect/terminal state must follow the durable append boundary. The terminal record contains the completed assistant response ref so a crash cannot turn missing delta chunks into an apparently complete answer.

## 4. Exclusive ownership and append durability

### 4.1 Writer acquisition

1. Resolve and validate state root ownership and session ID without following unsafe symlinks.
2. Open/create the stable `writer.lock` inode, then acquire an OS-enforced nonblocking exclusive lock. Keep the descriptor open until all child cleanup and journal writes finish.
3. If locked, return `session_busy` with non-secret owner metadata; do not wait indefinitely or steal it because the metadata PID appears absent.
4. Write advisory lock metadata containing invocation ID, boot identity, PID/start identity and acquisition time. The lock itself remains authority; metadata is diagnostic.
5. Open journal and validate it before admitting provider/tool work. Readers may obtain a validated snapshot up to a specific complete hash/sequence; they never mutate the journal during writer recovery.

Unix v1 may use `flock` or an equivalently qualified advisory lock; all writers in the product must honour it. Network filesystems with unqualified locking/sync semantics are unsupported for writable state. An unsupported backend returns `state_filesystem_unqualified`, not a volatile fallback. The spec does not claim a filesystem lock blocks a malicious process ignoring it.

### 4.2 Append algorithm

For each acknowledged authority event:

1. Validate the next state transition and all referenced object availability/hashes in memory under writer ownership.
2. Encode the complete bounded canonical JSON line, including chain hashes, before writing any bytes.
3. Append with a short-write-aware loop; do not assume a single `write` or `O_APPEND` makes a large line crash-atomic. No other writer can interleave because the lock is held.
4. Flush userspace buffers and call the qualified durable sync primitive on the journal (`fsync`/`fdatasync` as supported). Only now advance the acknowledged in-memory durable seq/hash and present authority-bearing completion to consumers.
5. Optionally replace the derived manifest atomically, syncing the new file and parent directory where required. A manifest update failure does not invalidate an already durable journal append; report/rebuild the cache before relying on it.

Text-only deltas may be grouped into a bounded sync batch, proposed maximum 100 ms or 64 KiB. Such deltas remain provisional until sync. Effect intent, checkpoint-ready, permission decisions authorising effects, effect result and terminal turn events force immediate durability; they cannot wait for the text batch window.

If append or sync fails, stop further tool admission immediately. Do not continue on the basis of an in-memory event, and do not claim bytes are durable because `write` returned success. Persist a failure report only if a safe durable path remains; otherwise stderr indicates failure and restart performs recovery. No periodic background flush can substitute for this boundary.

### 4.3 Object persistence before reference

Write immutable object bytes to a random private temporary file in the target filesystem; enforce size/redaction policy; compute digest and size while streaming. Sync bytes, atomically install at the digest path, and sync the containing directory under the qualified platform recipe. If an existing digest path exists, verify its full bytes/size before reuse. Only then append a journal event referencing it. A collision/mismatched existing object is `object_integrity_failure`; never overwrite it silently.

An object written without a journal reference after crash is an orphan, not accepted evidence. A journal reference to a missing/incomplete object is a recovery error. Cross-filesystem rename does not meet the recipe; stage on the target filesystem. The store must surface what its durability qualification covers; it cannot guarantee survival of all hardware/controller failures merely by naming `fsync`.

## 5. Replay, corruption and crash windows

Replay validates from the beginning or from a verified snapshot bound to a specific seq/hash. It builds a pure projection: messages, call pairing, active turn, effects, process handles, instruction/context snapshots, usage and checkpoint index. A snapshot cannot skip validation of missing prior required objects. Full history remains retrievable within retention.

| Observation at open | Required behaviour |
|---|---|
| Empty new session | Writer may append `session.created`; no phantom prior session. |
| Complete newline-terminated valid records | Replay in sequence; derive manifest from validated state. |
| Final physical line lacks LF and is invalid/incomplete | Quarantine exact trailing bytes, sync quarantine/report, then truncate only that tail under writer ownership and sync. Append a recovery disposition identifying original length/hash and valid boundary before resuming. |
| Final line lacks LF but parses valid JSON | Treat as unacknowledged tail; quarantine rather than silently accept. It may have been written before the sync/ack boundary. Reconcile effects from filesystem/process evidence. |
| Invalid JSON/hash/sequence in a nonfinal line | `journal_corrupt`; retain all bytes; no automatic skip/truncation or model contact. Read-only forensic export permitted. |
| Valid record with missing referenced object | `object_missing`; recovery-required; cannot fabricate empty output or omit the event. |
| Manifest older than journal | Rebuild cache from replay; no event rerun. |
| Manifest newer than journal | Ignore cache as authority, report inconsistency and require recovery disposition. |
| Duplicate event ID or sequence | Identical duplicated physical record is still invalid journal structure; do not silently double-account or skip. A replay subscriber may deduplicate transport delivery separately. |
| Stale active invocation, no live writer lock | Record prior invocation interruption after reconciling effects; create fresh invocation ID. |

A valid-looking tail event may have reached disk without the process observing its sync result. Recovery treats physically valid complete records as journal history but still reconciles the affected external result; a process's lost acknowledgement does not erase the record. A non-LF tail is conservatively unacknowledged and quarantined as above. The product does not promise perfect attribution of the last storage write after power loss; it promises visible safe disposition.

### 5.1 Failure-window matrix

| Crash point | State after restart | Permitted recovery |
|---|---|---|
| Before effect intent sync | No authority to run should have been used | If external change exists nonetheless, flag invariant violation/unattributed change; never assume it belongs to this effect. |
| Intent durable; before process/file operation | Pending intent | Prove absence/not-started where possible; record disposition. Do not launch automatically solely from replay. |
| After spawn before process identity append | Launch outcome uncertain | Inspect durable launch nonce/owned containment; if not attributable, quarantine and require disposition before replacement. |
| After file transaction replaces one path | Partial transaction | Use stored plan/progress and pre/post hashes to complete or restore only safe task-owned paths. |
| Effect known complete; result object durable; journal result absent | Unacknowledged observed effect | Verify exact object/postimage/process exit evidence and append reconciled result; no re-execution. |
| Result durable; stdout response lost | Completed effect/history | Replayed query returns same durable result; output delivery failure is separate. |
| Terminal turn record absent | Interrupted/incomplete turn | Append new recovery observation; do not claim the old task completed. |
| Sync fails while journalling cancellation | Cancellation locally requested, durability unknown | Stop effects anyway; report persistence failure and recover on next invocation. |

## 6. Session resume and project continuity

A resume verifies exact session/project/workspace binding, writer compatibility, all necessary objects, unfinished effects and current filesystem identity. Current cwd differing by a symlink spelling to the same canonical root is not a relocation. A moved root with matching repository metadata is a candidate relocation requiring explicit authenticated user acknowledgement; store a new root-binding generation and old/new evidence. Matching origin URL alone is insufficient. Headless resume cannot answer this decision by reading model output; fail with actionable `project_relocation_required`.

Do not silently import old platform journals. A future explicit importer must record source format/revision/hash, field mapping, excluded history and redaction. Imported `done`, success text or fake/scripted-provider events remain labelled historical and cannot qualify new real runtime journeys.

After interrupted provider-only work, the new turn may reconstruct conversation from completed exchanges and explicit interruption messages. Partial assistant text is retained as partial, not promoted to a completed response. Incomplete tool groups are projected into explicit cancelled/unknown results only after effect reconciliation; valid provider pairing must be restored before another request. Original stored events are never rewritten to simplify the provider transcript.

If policy or instructions changed since the previous invocation, persist new snapshots and re-evaluate future effects. Old approvals do not silently widen to new paths/arguments. Already observed outcomes remain historical under their original policy. Unsupported older binaries must refuse writable opening of a newer store, not downgrade it in place.

## 7. Raw output, logs, privacy and budgets

An `ArtifactRef` contains `{schema_version:1,digest,size_bytes,media_type,encoding,storage_key,redaction_profile,complete}`. `storage_key` is an opaque store-relative identifier; tools receive no arbitrary absolute state path. Retrieval validates session ownership, current policy, digest and bounds. A model-visible reference can retrieve only its authorised object's bytes; it cannot become a generic filesystem read primitive.

Tool display output has a declared `truncated` flag and exact omitted byte/line count when known. Full **redacted** bytes are retained under `raw_ref` if within quota. If capture itself reached a limit, `complete:false` and capture boundary are explicit; no subsequent retrieval can pretend the omitted bytes exist. Secret values resolved from configuration must be redacted before persistence; private file content remains local and may be sent only under user/policy authorisation. Best-effort pattern redaction is labelled as such; it cannot guarantee removal of every unknown secret.

Command stdout/stderr use ordered per-stream chunk IDs and byte offsets, with a final stream manifest naming all chunk hashes and completeness. Preserve separate stdout/stderr ordering; do not invent a total cross-stream byte order not observed by the collector. Expose interleaving observation events if useful. Reader backpressure must not deadlock a child; capture streams concurrently, spool within limits and apply explicit truncation/termination policy.

| Proposed limit | Value / consequence |
|---|---|
| Session journal record | 1 MiB maximum physical line; larger content stored by reference. |
| Inline tool result | 32 KiB UTF-8, chunk/range-safe truncation and retrievable ref. |
| Single captured tool artifact | 16 MiB default; larger capture requires explicit configured allowance or marked incomplete. |
| Checkpoint single file | 8 MiB default; noncapturable files block protected mutations. V1 has no non-reversible checkpoint bypass, including unrestricted mode. |
| Session durable storage | 512 MiB soft threshold, 1 GiB hard default; preflight expected checkpoint/output space before admitting effects. |
| Instruction file | 256 KiB maximum each; no silent truncation of binding instructions. |
| Instruction bundle | 1 MiB bytes and model-token budget check; overflow blocks affected operation pending explicit narrower context. |
| Retained checkpoint turns | 100 default; referenced/unresolved/recovery-held checkpoints cannot be evicted solely to satisfy count. |
| Raw-result read | 32 KiB UTF-8 bytes per retrieval page; `retrieve` may accept `max_chars` up to 65536, but the common 32 KiB byte cap still bounds returned content. Stable byte/line ranges and explicit next cursor. |

Disk quota handling prioritises pending authority/recovery data over disposable caches. At soft threshold expose warning and offer explicit cleanup. At hard threshold stop effect admission; do not delete active evidence or user files automatically. A failed disk-space preflight can still race external disk use; runtime must handle real `ENOSPC` at every persistence boundary.

## 8. Checkpoints and rewind ownership

A checkpoint is per turn and per effect/path observation, not a repository reset. The baseline may already contain dirty tracked files, untracked files, executable mode changes and unrelated user edits. Capture exact bytes/mode/existence for every path about to be changed by a managed file tool, including the first preimage of an already-dirty file. An agent-created path records `before.exists:false`; deleting it on rewind requires its current bytes still equal the agent's recorded postimage.

```text
CheckpointManifest {
  schema_version:1, checkpoint_id:Id, session_id:Id, turn_id:Id,
  project_id:Id, root_generation:int, created_at:UTC,
  entries:list[{
    root_id:Id, path:str, operation:"create"|"update"|"delete",
    before:{exists:bool,digest:Digest|null,mode:int|null,object_ref:ArtifactRef|null},
    after:{exists:bool,digest:Digest|null,mode:int|null},
    effect_ids:list[Id], capture_status:"complete"|"excluded"|"too_large"|"failed",
    attribution:"managed_tool"|"observed_shell"|"unattributed"
  }],
  exclusions:list[{path,reason}], complete:bool
}
```

Symlinks, directories, hardlinks, sparse files, executable bits and file type changes require explicit supported semantics. V1 file tools may refuse symlink/hardlink targets and nonregular file types rather than claim safe preservation. Mode changes are part of pre/post identity; matching content alone is not enough. Never traverse a symlink introduced after capture. Secret-shaped files follow the sensitive-path policy and are not copied casually into checkpoint archives. If excluded capture makes required rewind impossible, deny before changing them. V1 has no non-reversible checkpoint bypass; unrestricted mode and generic approval cannot waive mandatory capture.

### 8.1 Rewind protocol

1. Select an exact target turn/checkpoint range; reject nonexistent/pruned IDs and report missing recoverability instead of rounding to the nearest one.
2. Compute preview from durable manifests and current filesystem hashes. Show agent-owned operations, before/after sizes, excluded paths, conflicts and effects beyond file restoration.
3. Acquire the required permission for the exact preview digest. Recheck policy, canonical path, current postimage and root generation before staging; stale previews cannot execute.
4. Build a transaction plan restoring only eligible entries in reverse dependency order. A path changed externally since its agent postimage is a conflict. Default is refuse the whole requested rewind before mutation; user may explicitly select a conflict-free subset as a new plan.
5. Persist plan, staged replacement objects and progress intent; then replace/delete entries using the recoverable transaction protocol. Append results per operation and final terminal receipt.
6. Append a new rewind event and update future context projection. Retain original turns/effects and record that their file changes were reverted; do not erase history.

Rewind never undoes external network calls, commits/pushes, processes' nonworkspace effects or unknown shell effects. Its receipt lists those limitations. `git reset --hard`, `git clean` and restoring the repository from HEAD are forbidden implementations of this contract because they discard unrelated user state.

### 8.2 Multi-file transaction / recovery

The transaction plan contains ordered root/path operations, before/after hashes and modes, staged object refs, backup refs, expected root generation, policy decision, checkpoint ID and transaction ID. Stage every output and required backup before first commit. The plan digest is immutable. Append `PREPARED` only after all refs are durable; then `COMMITTING` and each observed replacement; finish `COMMITTED` only after postimage verification and relevant directory syncs.

Filesystem replacement is atomic only per supported individual rename, not across the whole plan. A crash can expose a mixed set. On restart classify each path as exact-before, exact-after or divergent. A complete exact-after set can be recorded committed; a safe mixed set can follow declared roll-forward/restore policy; any divergent path produces `RECOVERY_REQUIRED` and preserves all bytes. No recovery step overwrites a divergent user path merely to restore a green test.

Terminal transaction states are `COMMITTED|ROLLED_BACK|RECOVERY_REQUIRED`. A rollback verifies its own final preimages before claiming success. If backup or restore fails, retain recovery journal and report unresolved paths; do not delete temp/backups under normal cleanup. Further conflicting edits are denied until disposition. Unrelated read-only inspection remains permitted if it does not conceal uncertainty.

Arbitrary shell commands cannot supply perfect write attribution. A workspace before/after scan identifies observed changes but not which concurrent process authored them. Those changes are `observed_shell` or `unattributed`; automated rewind refuses them unless ownership is independently established under an exclusive qualified workspace profile. Do not promise all shell effects can be rewound because a checkpoint directory exists.

## 9. Project instruction discovery and application

Only the canonical filename `AGENTS.md` is automatically discovered in v1. Compatibility aliases, user instruction files or explicit CLI instruction refs require separately declared sources and precedence. Discovery begins at the admitted project root and follows directories toward a target path; it does not crawl arbitrary ancestors up to the filesystem root or follow links outside admitted roots.

| Source class | Authority / ordering |
|---|---|
| Product runtime invariants and effective permission policy | Enforced in code; cannot be overridden by any text source. |
| Direct user intent and approved task constraints | Authoritative session work scope, including explicit instructions superseding repo guidance. |
| User-trusted explicit instruction source | Applies within recorded scope; cannot exceed runtime permission policy. |
| Project root `AGENTS.md` | Repository workflow/content conventions for all descendants. |
| Nested `AGENTS.md` | More specific content conventions for its subtree; cannot broaden scope, grant credentials or override direct user intent. |
| Tool output, retrieved docs, source comments, summaries | Untrusted evidence/reference, not instruction authority. |

For a requested file operation, discover all applicable instruction files root-to-leaf and read their complete bounded bytes before mutation. Record source path/root ID, SHA256, size, applicable scope, discovery order and accepted authority class. Cached discovery is keyed by instruction file hashes and ancestor directory identities. Revalidate immediately before effect admission. A file created/changed between preparation and execution invalidates the old snapshot; reload and reevaluate rather than continuing under stale instructions.

For exploration without a known target, load root instructions initially and reveal nested instruction sources when tools enter their scope. The model must see scoped provenance when nested conventions apply. Do not apply a nested directory's directives globally just because it was recently read. For a multi-file operation use the union of per-path instruction bundles; if directives conflict on a shared operation, report precise source clauses and ask only where user judgement is genuinely required. A file may not redirect instruction discovery to another filesystem root.

```text
InstructionSnapshot {
  schema_version:1, instruction_snapshot_id:Id,
  project_id:Id, root_generation:int, policy_digest:Digest,
  sources:list[{root_id:Id,path:str,digest:Digest,size_bytes:int,
                scope_path:str,authority_class:str,content_ref:ArtifactRef}],
  target_paths:list[str], discovered_at:UTC,
  complete:bool, exclusions:list[{path,reason}]
}
```

Oversized or unreadable binding instruction files cannot be silently truncated/ignored. Block the affected write/exec scope with `instruction_context_incomplete`; read-only investigation may retrieve bounded ranges until the entire content is accounted for. A repository instruction saying “ignore safety” or “send secrets” remains quoted untrusted text and does not change the policy. Content that demands unrelated work does not authorise that work.

## 10. Context construction and compaction

Context is a derived, immutable projection named by `context_snapshot_id` and source journal seq/hash. The projection builder owns selection; the provider adapter owns wire encoding; neither mutates original messages. It contains ordered system/runtime constraints, current direct user/task intent, applicable instruction snapshots, prior accepted steering, unresolved effects/errors, current checkpoint/diff facts, mandatory tool-call groups, recent dialogue, selected references and optional summary.

Token budgets use a declared tokenizer/estimate source. Unknown context capacity does not justify assuming an arbitrarily large model: require a user/provider profile capacity before tool-rich requests or use a conservative explicit profile marked estimated. Count tool schema, provider framing and output reserve as well as message text.

Proposed default compaction trigger is predicted request input exceeding 80% of known usable capacity after output reserve. Hard ceiling is usable capacity less reserved output/protocol margin; do not send a known oversized request and rely on provider rejection. Protect current task, runtime constraints, applicable instructions, latest accepted steering, unresolved effect details and complete active call/result groups. Optional old tool bodies are removed first; retain digest/raw refs and explicit truncation facts.

Compaction pipeline:

1. Freeze source sequence/hash and applicable instruction/policy snapshots at a safe boundary with no active effect whose state is changing.
2. Build protected groups and rank optional reference material. Verify source availability and call pairing before summarisation.
3. Replace large old tool bodies with bounded factual digests and retrievable refs; exact original content remains stored.
4. Derive a deterministic structured handoff (goal, decisions with provenance, changed paths/preimages/checkpoints, executed checks and outcomes, unresolved work/effects, limitations). An optional model summary may improve prose but cannot change protected facts or authority.
5. Validate the candidate projection: no orphan/duplicate tool results; no missing unresolved effect; instruction/task/policy identities retained; within budget; every summary assertion linked to original events or marked model inference.
6. Persist immutable projection/summary objects, then append `context.compacted`. Only then replace the active request projection. Failure keeps the previous projection and either narrows optional context or returns `context_budget_exhausted`.

```text
ContextProjection {
  schema_version:1, context_snapshot_id:Id,
  source_session_id:Id, source_last_seq:int, source_last_hash:Digest,
  instruction_snapshot_ids:list[Id], policy_digest:Digest,
  message_refs:list[MessageRef], retained_call_groups:list[Id],
  summary_ref:ArtifactRef|null, unresolved_effect_ids:list[Id],
  budget:{capacity_tokens:int,output_reserve:int,protocol_margin:int,
          estimated_input_tokens:int,estimator:str,complete:bool},
  omitted:list[{source_ref,reason,retrievable:bool}], projection_digest:Digest
}
```

A compacted summary must be labelled reference material and remain subordinate to the latest direct user request. It cannot copy an old task as an active command merely because the text said “next”. Do not use a fabricated `user` role to erase provenance; if provider wire compatibility requires a user-shaped summary block, retain a strict internal `generated_reference` origin and explicit delimiters. Any stale outstanding plan is described as historical until explicitly continued.

Restored projections revalidate source hashes, policy and current instructions. Compaction cannot grant fresh read-gate observations: the model must retrieve content and establish current preimage evidence before a stale edit. Evicting a body from context does not delete its artifact. Repeating compaction when recent savings were below 10% twice returns `compaction_ineffective` rather than spending unbounded model calls; the user/runtime may choose a larger supported model or narrower task under existing policy.

## 11. Store migration, retention and export

Store format upgrades are explicit versioned steps. Acquire writer lock; snapshot original metadata/journal/object manifest to an immutable backup; validate supported source version and migration prerequisites; write new files in a sibling staging location; validate projected equivalence; atomically switch a small version pointer under qualified filesystem semantics; retain recovery instructions. Never rewrite history in place without an exported original. Interrupted migration must select an unambiguous old/new state or remain recovery-required.

A downgrade must refuse incompatible stores. Source rollback and store rollback are separate: checking out an older commit does not make newer schema safe. No automatic deletion of old platform config/session directories is part of source prune.

Retention is reference-aware. Hold any object/checkpoint referenced by an active projection, unresolved effect, unfinished transaction, required verification receipt or user retention pin. Default is no automatic deletion of completed session history; offer explicit prune/export with preview. Removing a display catalog entry does not delete its journal. A user-requested deletion names an exact session/project scope and lists known evidence loss; it cannot use model-provided paths as authority.

GC first computes a stable reachability set under writer ownership, records candidate object digest list and retention reason, then deletes only unreferenced session-owned objects. Temp/orphan cleanup uses age plus absence of references and in-progress transaction holds; age alone is insufficient. It never traverses workspace paths, symlinks or another session. Interrupted GC can resume by digest and revalidation. A missing object not in the deletion manifest is integrity failure, not an acceptable GC race.

Export includes versioned manifest, journal bytes/hash, referenced objects, projections/checkpoints, redaction profile and explicit excluded/private content list. Verify the exported copy by hashes before reporting it usable. Importing an export into another project requires explicit rebinding and marks external process handles nonresumable. It cannot resurrect old approvals, child processes or claimed verification freshness.

## 12. Producer–consumer interfaces and semantic handoffs

Contract bindings: journal/envelope is `GC.Event/1`; session/append/replay is `GC.Session/1`; effect records are `GC.Effect/1`; canonical root/file observations are `GC.Workspace/1`; instruction/projection assembly is `GC.Context/1`; checkpoint/rewind plans are `GC.Checkpoint/1`; process observations are `GC.Execution/1`. All flow through the services constructed by `GC.Host/1`; there is no hidden global config lookup.

The implementation may split these responsibilities across several bounded tasks. A producer task is incomplete without its named consumer; a consumer may not invent defaults for absent required fields.

| Producer | Exact logical API / output | Consumer / must reject |
|---|---|---|
| `SessionStore` | `append(event)->DurableReceipt{seq,hash,event_id}`; `replay(up_to_seq?)->SessionProjection`; `read_object(ref,range)->VerifiedBytes` | Runtime waits for durable receipt at effect boundaries; rejects missing/hash-mismatched object or stale writer. |
| `EffectLedger` | `prepare(plan)->EffectIntent`; `record_start(effect_id,identity)`; `record_outcome(effect_id,result)`; `unresolved()->list` | Dispatcher uses the same effect ID and plan digest; recovery refuses blind repetition. |
| `InstructionLoader` | `snapshot(project,target_paths,policy)->InstructionSnapshot`; `validate_current(snapshot)->unchanged|changed|error` | Context builder carries snapshot identity; mutation revalidates it immediately before admission. |
| `ContextBuilder` | `build(session_projection,instructions,capacity)->ContextProjection` | Provider encoder checks tool pairing, capacity and provenance; cannot drop mandatory blocks silently. |
| `CheckpointStore` | `prepare(paths,turn)->CheckpointManifest`; `record_postimages(effect,result)`; `preview_rewind(target)->RewindPlan` | File tools require complete recoverable capture; rewind rejects divergent postimages. |
| `TransactionEngine` | `stage(plan)->PreparedTransaction`; `commit(id)->TransactionReceipt`; `recover(id)->Disposition` | Effect ledger records exact transaction ID/outcome; UI cannot call staged work committed. |
| `ProcessRegistry` | `register_launch(intent,identity)`; `observe(id)->ProcessObservation`; `retire(id,deadline)->StopReceipt` | Recovery/cancellation require boot/start identity; never signal by raw PID alone. |

Handoff packet for a split implementation task must name the exact shared type version, producer file/symbol, consumer file/symbol, invariant IDs covered, mandatory negative cases and unresolved interface questions. Changing a shared field or meaning requires updating both producer and consumer in one integration packet or an explicitly staged compatible version. A passing producer unit check does not qualify the integrated consumer scenario.

## 13. Deterministic acceptance registry

Local invariant traceability to [architecture.md](./architecture.md): S-01/S-02/S-04 refine INV-01; S-03 refines INV-03/05; S-05/S-06 refine INV-08; S-07 refines INV-04/06/17; S-08 refines INV-10; S-09 refines INV-07/10; S-10 refines INV-05/09/13; S-11 refines INV-15/17; S-12 qualifies the local threat-model limits of INV-01/03. Exact producer–consumer handoffs and focused gates may be split into parallel packets; no macro task's size overrides the scope/ownership restrictions in [parallel-execution.md](./parallel-execution.md).

Every row is **UNRUN**. Oracles inspect real temporary files/processes/journals; source greps or synthetic success strings do not satisfy them. Fault schedules identify each injected sync/write/rename boundary and the exact expected invariant. Controlled fault injection into real filesystem paths is valid recovery evidence; it is not real-model coding evidence.

| ID | Inputs / fault schedule | Observable acceptance / invariant |
|---|---|---|
| SC-001 | Create, append, close and reopen a real session with message/call/result objects. | Exact order/IDs/bytes restore; derived manifest matches journal; no effect executes on replay. S-01/S-05. |
| SC-002 | Two actual processes concurrently open the same session; kill lock holder and retry. | One writer; contender gets busy; lock released by OS on death; no stale-PID lock theft. S-02. |
| SC-003 | Inject short write and crash before LF, after LF, before/after sync. | Valid prefix intact; torn tail quarantined with exact bytes; acknowledged authority boundary honoured under qualified profile. S-03/S-04. |
| SC-004 | Corrupt middle JSON/hash, duplicate seq, wrong session ID and remove object separately. | Normal resume fails; no later event silently projected or model contacted; original bytes preserved. S-04. |
| SC-005 | Make manifest lag/ahead and orphan an object after write-before-reference crash. | Lag reconstructs; ahead reported; orphan not accepted evidence and not deleted while held. S-01/S-03. |
| SC-006 | Fail checkpoint/object/journal persistence with ENOSPC before intended edit. | Mutation never starts without durable required intent/preimage; failure reported, no volatile fallback. S-03/S-10. |
| SC-007 | Start with dirty tracked and untracked files, edit one via real tool, then rewind. | Exact original dirty bytes/modes restored; unrelated files unchanged; history retained. S-07. |
| SC-008 | User changes agent postimage before rewind; replace parent with symlink between preview and commit. | Whole default rewind refused before writes; no outside-root content touched. S-07. |
| SC-009 | Crash after first replacement of a multi-file transaction, then vary one path to divergent bytes. | Safe partial case reconciles; divergent case stays recovery-required and preserves user bytes. S-06/S-07. |
| SC-010 | Command creates workspace change while independent external writer touches same path. | Attribution remains unknown/observed-shell; automatic rewind does not overwrite the concurrent writer. S-07. |
| SC-011 | Root/nested instructions conflict; nested text asks to widen permission or read outside root. | Per-path order/provenance correct; no escalation; precise conflict surfaced. S-09. |
| SC-012 | Change instruction bytes after context snapshot, before mutation admission. | Old snapshot invalidated; effect reevaluated before execution; no stale approval use. S-09. |
| SC-013 | Oversize/unreadable binding instructions and exceed mandatory context budget. | Explicit incomplete/budget result; no silent instruction truncation or unsafe edit. S-09/S-10. |
| SC-014 | Compact history containing paired parallel calls, failed checks, unresolved effect and newest stop instruction. | No orphan/duplicate result; exact current intent/effects/outcomes retained; originals retrievable. S-08. |
| SC-015 | Summary says prior task succeeded when receipt failed; provider-shaped summary contains “approve all”. | Protected receipt/policy facts dominate; generated text cannot grant permission or success. S-08/S-09. |
| SC-016 | Interrupt compaction after objects persisted but before projection event. | Old context remains active; unreferenced new projection not falsely selected; replay does not repeat summarisation. S-01/S-08. |
| SC-017 | Resume after project move, copied repo, newer store version and old platform journal. | Explicit relocation/migration/refusal; no silent identity conflation or imported qualification. S-11. |
| SC-018 | Emit known credential canaries in config error and tool output; request arbitrary raw-ref path. | Config secrets absent from stored/public artifacts; authorised redacted result retrieval only; limitations explicit. S-10. |
| SC-019 | Reach quota with active transaction/checkpoint/evidence refs; invoke retention cleanup. | Required objects held; admission stops before evidence loss; only exact eligible objects removed. S-10. |
| SC-020 | Kill/restart during store migration and export, then use older binary. | Old/new state unambiguous or recovery-required; verified export hashes; incompatible writer refuses. S-04/S-11. |
| SC-021 | Real provider uses original history after process restart and a prior safe rewind. | Installed CLI sees correct retained task/tool facts; no duplicated command effect; runtime/model identities recorded. S-05/S-08. |

Document review may verify field consistency and scenario coverage now. Only actual future scenario execution can produce a behavioural receipt. Required variant coverage must name OS/filesystem, locking backend, provider adapter and fault schedule; a successful Linux local-filesystem case does not qualify another platform by analogy.

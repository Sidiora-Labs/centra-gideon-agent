# Gideon Code runtime and CLI contract

Status: **PROPOSED; implementation pending; every acceptance scenario is UNRUN.** This chapter specifies required behaviour of the extracted package. An existing source reference proves only that the named source exists and informed the design; it does not prove the proposed package, API, command or scenario works. The canonical task/status authority is [spec.kvx](./spec.kvx). Durable event and recovery rules are defined in [storage-context.md](./storage-context.md). Permission, edit, execution and extension contracts may refine adapters but must preserve this chapter's lifecycle and uncertainty rules.

## 1. Purpose, boundaries and vocabulary

The product is a local CLI coding agent: one invoked process owns its conversation, provider stream, policy decisions and child processes. It must operate in a repository without a gateway, token minting service, network listener, personal-memory service, Hypermid, Node or Rust build dependency. Calling a remote model is permitted and does not constitute a local gateway. Optional MCP stdio peers are children, not a prerequisite for the core.

The same runtime executes interactive and headless turns. Surface adapters render events and collect authenticated user input; they cannot implement a second tool dispatcher, approval path, persistence policy or provider retry loop. A successful model answer is distinct from a successful tool, a passed check and a completed programming task.

| Term | Exact meaning |
|---|---|
| Session | Durable ordered conversation bound to one project identity and one active writer. |
| Invocation | One operating-system CLI process; may create or resume a session. It receives a fresh `invocation_id`. |
| Turn | One accepted user request plus any explicitly attached steering, model exchanges and tools up to a terminal turn record. |
| Exchange | One provider request and its response stream; retries have distinct exchange IDs. |
| Call | One complete model-proposed tool call, identified by its original provider call ID inside its exchange. |
| Effect | One admitted attempt to produce an observable external change or execute a process; identified independently of provider call IDs. |
| Result | Durable observed outcome of a call/effect; model narrative is never its source of truth. |
| Completion | Terminal runtime outcome. `completed` means normal turn termination; it does not certify every requested acceptance condition. |
| Verification receipt | Actual execution evidence naming command, source identity, exit/signal and retrievable output. |
| Unknown | Evidence is insufficient; it is neither zero, success, failure nor permission to repeat an effect. |

### 1.1 Existing seams and required changes

| Observed source | Reuse intent / required difference |
|---|---|
| [gateway-backed headless CLI](../../runtime/gideon/interfaces/cli/run.py) | Preserve useful output semantics, replace gateway discovery and local-secret/token requirements with in-process construction. Existing read-only task-mode enforcement must not be lost when the gateway is removed. |
| [ModelProvider](../../runtime/gideon/integrations/llm/base.py:73) | Reuse provider streaming/cleanup capabilities; separate inference from approval delivery and platform agent-session ownership. |
| [AgentEvent](../../runtime/gideon/integrations/llm/events.py:53) | Translate existing events; introduce presence-aware nullable usage rather than treating the existing zero defaults as observed billing. |
| [NativeAgentRuntime.stream](../../runtime/gideon/engine/agents/native/runtime.py:1351) | Preserve model→tool→model loop, terminal outcomes, cancellation and tool pairing; inject lean interfaces instead of importing platform registries. |
| [conflict-aware dispatch](../../runtime/gideon/engine/agents/native/dispatch_plan.py:199) | Retain read/write reservation reasoning; parallel output arrival must not reorder conversation tool results. |
| [CancelScope](../../runtime/gideon/core/cancellation.py:104) | Retain turn ownership and bounded child retirement; durable effect uncertainty and restart identity require explicit additions. |

These are extraction candidates, not a promise to copy complete modules. Transitive and lazy imports must satisfy the new boundary before a candidate is considered extracted.

## 2. Normative invariants

R-01. No provider delta, repository instruction, tool output, hook, child message or saved summary can grant permission, replace user intent, alter configured limits or mark a check passed.

R-02. Every admitted user turn has exactly one durable terminal turn record. If the process dies before that record exists, recovery records interruption; it does not invent a previously emitted success.

R-03. No tool runs from an incomplete provider response. A complete call requires a stable original call ID, known canonical tool name and fully parsed validated argument object.

R-04. Every tool call accepted into conversation has exactly one terminal result projection. Calls cancelled before dispatch receive `status:cancelled` with `error.code:cancelled_before_run`; unknown executed effects receive `outcome_unknown`, never a synthetic failure implying no effect happened.

R-05. Effect intent is durable before execution; effect outcome is durable before the runtime presents the operation as completed. Persistence failure stops further effects.

R-06. Replaying conversation records rebuilds state. It must never rerun shell, edit, hook, MCP or network effects automatically.

R-07. Cancellation closes admission before awaiting provider/child cleanup. Late provider completion cannot reopen a cancelled turn.

R-08. All generated text and streamed tool output is untrusted presentation data. Terminal control bytes, apparent approval prompts and forged event-shaped strings remain data.

R-09. Unknown cost, model identity, token usage, process outcome and sandbox capability remain explicitly unknown. No UI formatting or aggregate arithmetic converts unknown to a measured zero.

R-10. A headless invocation requiring a human decision returns a typed refusal without waiting on stdin indefinitely, unless an explicit separately authenticated approval input route has been selected.

R-11. One session has at most one active writer. Parallel subagents use their own sessions, scopes and workspaces; they do not append to the parent journal directly.

R-12. A budget is checked before admission and after observed usage. A configured limit cannot promise prevention of all billed overrun when the provider supplies usage only after completion.

## 3. CLI grammar and input ownership

The following is the proposed v1 command surface; option spellings are normative and must appear in generated help when their owning task lands.

```text
gideon-code [--cwd PATH] [--model ALIAS] [--config FILE]
            [--resume SESSION_ID] [--permission read-only|ask|unrestricted]
            [--format text|jsonl] [--max-turns N] [--timeout SECONDS]
            [--max-cost-usd DECIMAL] [-p PROMPT]
gideon-code doctor [--format text|jsonl]
gideon-code inspect COMPONENT [COMPONENT_ARGS...]
gideon-code sessions list [--cwd PATH] [--format text|jsonl]
```

`-p` selects exactly one headless user turn. Without `-p`, a TTY starts the interactive session. Without `-p` and without a TTY, fail with exit 2 and an instruction to pass `-p`; do not silently read an unbounded pipe. The empty string after `-p` is invalid. The literal `-` is ordinary prompt text in v1; stdin-prompt support requires a later explicit contract. Prompt text and argv must contain no NUL bytes. Reject repeated exclusive options rather than silently taking whichever parser happens to prefer.

`--cwd` is resolved against the caller's original cwd before config discovery. Fail if missing, not a directory or inaccessible. Preserve the caller's cwd in the invocation record; do not mutate the parent shell. Discovery never changes cwd in response to a file's contents. A nested repository is a distinct project when Git identifies a distinct common directory.

`--resume` names an exact session, never a fuzzy prefix. Ambiguous, inaccessible, corrupt or incompatible sessions fail before model contact. A different project requires an explicit user relocation decision as specified by the storage contract. Read-only listing does not acquire a writer lock or contact a provider.

`--model` selects a configured alias. It is not a request to create a new provider identity. Unknown alias or unsupported required capability fails before model contact. A currently active exchange retains its pinned model; interactive model changes apply to the next exchange after the previous one has terminated.

`--permission unrestricted` is an explicit invocation-level policy choice and must be shown on stderr/start metadata. It does not disable project boundaries, secret protection, effect journalling, recovery, environment budgets or declared external-action authorisation. Plan mode always narrows its permissions.

### 3.1 Packaged component consumers

`gideon-code inspect COMPONENT [COMPONENT_ARGS...]` is the bounded `GC.Host/1` composition seam used to qualify independently delivered vertical capabilities before their macro integration enters the default agent workflow. The component descriptor inside the installed distribution specifies its exact argument schema, contract major version, required services, permitted effects and help. Unknown component, unknown arguments, absent required service or incompatible contract version fails with exit 2; no zero-selection success is permitted.

An inspection consumer must execute the real component through the same host configuration, policy, workspace, persistence and cancellation adapters used by the default workflow. For example, a session consumer creates/reopens a real private temporary session under an explicitly supplied disposable state root; a file consumer reads actual scoped fixture bytes; a process consumer observes a real owned process. It may not return a canned success report, substitute a scripted model, or bypass a permission/storage dependency merely because the command is diagnostic. Components needing real inference require the selected already authorised provider; missing credentials are blocked, not simulated.

Descriptors are read only from the installed in-package namespace. Repository/user config, skills, MCP text, `PYTHONPATH` additions and model output cannot register executable components. Loader validation rejects duplicate component IDs/consumer verbs, import modules outside the distribution, unsupported schema majors and descriptors claiming a broader permission profile than the invocation. Inspect defaults to read-only and creates no effect solely by listing help. An effectful consumer requires explicit fixture/workspace inputs and current permission admission; it is not a hidden escape from the normal tool gate.

The bootstrap host owns the shared router and service contracts. Independently concurrent feature packets own their component module/descriptor/consumer/checks, not shared CLI/agent assembly files. Macro integration packets alone modify those shared files. A packet handoff names exact `GC.*` major versions and producer/consumer symbols; missing semantics block integration rather than being guessed by the next worker. See [architecture.md](./architecture.md) and [parallel-execution.md](./parallel-execution.md).

### 3.2 Interactive commands

| Command | Operation and boundary |
|---|---|
| `/model [ALIAS]` | Inspect selection or request next-exchange selection; report unresolved/served identities separately. |
| `/plan` | Enter read-only planning mode at a safe exchange boundary; queued mutations are not executed under the previous mode. Leaving planning requires explicit user action, not plan prose. |
| `/diff` | Render actual agent-owned current file changes; preserve and identify unrelated user changes. No model call required. |
| `/rewind TURN_ID` | Preview checkpoint plan, obtain required approval, then use conflict-aware rewind. Never run a repository-wide hard reset. |
| `/compact` | Request context projection compaction at a safe boundary; journal remains append-only. |
| `/resume SESSION_ID` | Close/quiesce the current session, release its writer lock, then acquire/validate the target; no two live writers during switching. |
| `/quit` | Stop accepting user turns, retire owned processes under policy, persist closure and exit. |

A line beginning `//` is ordinary prompt text beginning with `/`; this escape lets users discuss slash commands. Unknown slash commands return a local error with valid command names; do not send them to the model. Commands requiring idle state either wait visibly for the current safe boundary or reject as `turn_active`; they never race mutable runtime state. Multiple queued user prompts remain separate ordered turns. Steering is separately labelled and does not implicitly replace the original task.

### 3.3 Output and exit contract

Text mode sends assistant text and requested final results to stdout; progress, policy notices, errors and approval prompts go to stderr. Machine event output is JSONL only: no banners, ANSI, traceback or provider diagnostic text on stdout. Every JSONL object is emitted in one complete newline-terminated record. A closed stdout is a consumer disconnect: stop admitting tools, cancel the invocation and persist available evidence; do not continue editing in the background.

| Exit | Stable meaning | Example |
|---|---|---|
| 0 | Normal `completed` terminal outcome durably recorded | Answer delivered; tool/check statuses remain in its structured result. |
| 1 | Runtime/provider/persistence failure, incompatible recovery state or unresolved effect | Corrupt session, provider EOF without completion, process effect cannot be reconciled. |
| 2 | Invalid invocation/configuration or unavailable required configured capability discovered before work | Unknown alias, malformed config, invalid cwd. |
| 3 | Blocking permission refusal or required human approval unavailable | Headless write requires approval; no prior grant covers it. |
| 4 | Turn/admission/time/token/cost limit exhausted | Maximum exchange count reached with unresolved requested work. |
| 130 | Explicit user interruption | SIGINT during streaming or tool execution; cleanup status remains separately visible. |

`completed` does not mean a failed optional check passed. If the explicit requested task is to make a declared check pass, its known nonzero verification result prevents the task result from being `satisfied`; the runtime may complete its report normally. The final `task_status` is `satisfied|partial|failed|unverified`, independent of process exit. Automation consuming task success must inspect it; no success sentence can override the actual receipts.

Before a session exists, configuration failures use stderr and, in JSONL mode, one `invocation.error` object with `schema_version`, `invocation_id`, `error` and `exit_code`; it has no fabricated `session_id` or session sequence. After session creation the public stream uses the session event envelope.

Terminal outcomes follow the first durably committed terminal cause. Before any terminal cause has committed, explicit user interrupt wins over budget/refusal; persistence/corruption and unresolved effect recovery produce exit 1 while preserving `interrupted:true` if applicable. Once stdout has failed, no delivery guarantee for terminal output exists; the journal remains authoritative.

## 4. Configuration and model construction

Configuration is explicit UTF-8 TOML. Proposed search order, from lowest to highest precedence: compiled defaults; environment for documented option equivalents; user config at `$XDG_CONFIG_HOME/gideon-code/config.toml` (fallback `~/.config/gideon-code/config.toml`); project config at the selected project root `.gideon-code.toml`; explicit `--config` file; CLI flags. Unknown keys, duplicate keys, invalid numeric values and conflicting aliases fail. The effective config records each non-secret value's winning source.

Project configuration is untrusted repository input. It may set model aliases only from a user-declared allowlist, reduce budgets, select formatting and name existing policies. It cannot add a credential source, provider endpoint, executable hook, unrestricted permissions or wider roots without an explicit user trust decision bound to its content hash. It cannot make environment expansion a shell operation. Untrusted attempted escalation produces a diagnostic and an effective restrictive configuration; invalid safety-critical ambiguity fails closed.

| Field | Type / rule |
|---|---|
| `schema_version` | Integer exactly 1. |
| `default_model` | Configured alias, 1–128 UTF-8 bytes. |
| `providers.<alias>.kind` | `anthropic|openai-compatible`; optional future kinds require an adapter/capability declaration. |
| `providers.<alias>.model` | Nonempty configured provider model identifier; actual served identity separately reported. |
| `providers.<alias>.base_url` | Optional absolute HTTP(S) URL; no credentials in URL, fragments or implicit redirection to another origin with credentials. Non-TLS use requires explicit local/private endpoint choice. |
| `providers.<alias>.api_key_env` | Environment variable name; persist only its name, never resolved bytes. |
| `providers.<alias>.context_window` | Optional positive integer capacity override with source; unknown if absent and provider cannot report it. |
| `providers.<alias>.max_output_tokens` | Positive integer bounded by known provider capacity; do not silently omit unsupported values. |
| `permission.mode` | `read-only|ask|unrestricted`; default `read-only`. |
| `limits.*` | Validated positive values from section 10; effective minima compose with task/parent limits. |
| `fallbacks` | Ordered list of already configured aliases; default empty. Model substitution requires explicit enabled policy. |

Only the selected adapter and enabled extras are imported. Importing the core or printing redacted config must not require all provider SDKs. Credential resolution occurs at provider start, is scoped to that adapter, is never included in exceptions/events, and is not silently drawn from `.local_secret`, platform state or IDE settings. Explicit migration may import reviewed non-secret provider configuration; it must not create another identity by default.

Startup sequence is fixed: parse → canonical cwd/project identity → trusted config merge → validate capabilities/limits → acquire session writer → verify journal/recover → construct policy and injected roots → construct selected provider/tool inventory → persist start → open provider → accept turn. Recovery-required sessions cannot contact the model first and ask it to decide what happened.

## 5. Proposed typed in-process interfaces

Contract ownership: `GC.Host/1` owns service construction; `GC.Provider/1` owns `ModelRequest`, provider capabilities and complete delta assembly; `GC.Tool/1` owns `ToolCall`/`ToolResult`; `GC.Event/1` owns the public envelope; `GC.Effect/1` owns effect identity/lifecycle. A renamed Python class does not create a new semantic version. Serialized fields, units, null meanings and transition preconditions below must agree across their producers and consumers.

These are logical contracts, not a required public Python SDK or class hierarchy. Implementers may use small dataclasses/functions if they preserve shapes and ownership. No platform super-registry is needed.

```text
ModelRequest {
  schema_version: 1,
  exchange_id: Id,
  model_alias: str,
  messages: list[Message],
  tools: list[ToolSchema],
  max_output_tokens: int,
  sampling: {temperature?: finite number, reasoning_effort?: str},
  context_snapshot_id: Id,
  cancel_token: owned token,
  deadline_monotonic: number
}
ProviderCapabilities {
  streaming: bool, tool_calls: bool, parallel_tool_calls: bool,
  usage: "reported"|"partial"|"unavailable",
  cancellation: "acknowledged"|"best_effort"|"unavailable",
  context_window_tokens: int|null, served_model_identity: str|null
}
ProviderDelta = TextDelta | ToolArgumentDelta | UsageObservation |
                ProviderWarning | ExchangeComplete
ExchangeComplete {
  exchange_id: Id, stop_reason: "end_turn"|"tool_calls"|"length"|"refusal",
  calls: list[ToolCall], usage: Usage, provider_request_id: str|null
}
ToolCall {call_id: str, name: canonical str, arguments: object}
ToolResult {
  call_id: str, effect_id: Id|null,
  status: "completed"|"failed"|"cancelled"|"outcome_unknown",
  content: bounded object, raw_ref: ArtifactRef|null,
  truncated: bool, error: RuntimeError|null
}
RuntimeError {
  code: stable lower_snake_case str, message: redacted str,
  retryable: bool, details: bounded object, cause_id: Id|null,
  recovery_hints: list[bounded str]
}
Usage {
  input_tokens: nonnegative int|null, output_tokens: nonnegative int|null,
  cache_read_tokens: nonnegative int|null, cache_write_tokens: nonnegative int|null,
  total_tokens: nonnegative int|null, cost_usd: decimal-string|null,
  source: "provider"|"estimated"|"unavailable",
  scope: "exchange"|"turn"|"session", complete: bool
}
```

`ToolResult.status:completed` means the handler operation completed with its declared result, not that the user task or verification passed. Known tool failures and permission refusals use `status:failed`; `error.code` distinguishes `permission_denied`, `approval_required` and ordinary handler errors. Before-dispatch cancellation uses `status:cancelled` and `error.code:cancelled_before_run`. Textual content is wrapped in `content:{text:...}` rather than changing the content field type.

`Message` has immutable `message_id`, role (`system|user|assistant|tool`), ordered content parts, provenance and source event IDs. An assistant call list and its tool results form a pairing group. Tool role includes original `call_id` and internal exchange identity. Internal system constraints, user instructions, repository instructions, tool results and generated summaries retain distinct provenance even if a provider requires flattening to fewer wire roles.

Names translated for provider syntax must round-trip injectively. Two canonical names mapping to one sanitized name fail inventory construction; do not pick the last registered tool. A received unknown provider name is an invalid call result, never arbitrary registry lookup. Duplicate call IDs inside one exchange are protocol failure; the same textual provider ID in another exchange is scoped separately.

Provider request construction must preserve complete tool-call groups and token-limit settings. Unsupported options are rejected or exposed in `provider.warning` with the omitted option and reason before inference; no silent change to model, endpoint or reasoning mode. Private chain-of-thought must not be manufactured, required for operation, or persisted as if it were public assistant output. Provider-exposed concise reasoning summaries may be separate labelled optional content.

## 6. Session, turn, exchange and call state machines

### 6.1 Invocation/session

```text
CREATED → VALIDATING → RECOVERING → READY → CLOSING → CLOSED
                 ↘ FAILED        ↘ RECOVERY_REQUIRED
READY ↔ TURN_ACTIVE
```

Writer ownership spans `RECOVERING` through `CLOSED`. Transition to `READY` requires successful journal validation, current policy/instruction snapshot and no unclassified unfinished effect. Failure during initialization releases resources in reverse construction order. Cleanup failure retains its own record and may not erase the primary error.

### 6.2 Turn

| From | Trigger / guard | To and required action |
|---|---|---|
| `ACCEPTED` | Persist original user request and task limits | `PREPARING`; snapshot instructions and context. |
| `PREPARING` | Mandatory context fits and capability/policy valid | `INFERENCING`; persist exchange request identity before network contact. |
| `INFERENCING` | Valid complete assistant response with no calls | `FINALIZING`; attach assistant output and usage. |
| `INFERENCING` | Complete response contains calls | `DISPATCHING`; durably preserve assistant call group. |
| `DISPATCHING` | Policy needs authenticated decision | `WAITING_APPROVAL`; no affected effect starts. |
| `WAITING_APPROVAL` | Approval bound to exact plan and current policy | `DISPATCHING`; revalidate paths/preimages before execution. |
| `DISPATCHING` | Every call has terminal result, budget available | `PREPARING`; append ordered result group and build next request. |
| Any active | Cancellation or terminal budget/error/refusal | `CANCELLING` or `FINALIZING`; close admission and resolve known call statuses. |
| `FINALIZING` | Terminal record durable | `COMPLETED|FAILED|REFUSED|BUDGET_EXHAUSTED|INTERRUPTED|RECOVERY_REQUIRED`. |

Terminal states never transition back to active. A resumed session creates a new turn and may reference the old turn's interrupted intent; it cannot overwrite its terminal outcome. A tool returning an ordinary error is input to the next model exchange, not automatically a terminal turn error. Invalid protocol, persistence failure, containment failure and unresolved effects stop the turn.

### 6.3 Exchange and stream assembly

Exchange lifecycle is `REQUESTED → STREAMING → COMPLETE`, with `FAILED|CANCELLED|OUTCOME_UNKNOWN` alternatives. The adapter owns its HTTP/client stream; the runtime owns cancellation and deterministic cleanup. EOF without a valid final protocol marker is `incomplete_provider_stream` even if fluent text was received.

Tool argument chunks are buffered by provider index and original call ID; the adapter validates index/ID consistency, UTF-8, JSON object syntax, maximum bytes, nesting and schema only when the response is complete. No partial tool JSON executes. If one call is malformed, preserve its error and dispatch only fully validated independent calls permitted by the batch contract; a malformed call claiming dependencies or ambiguous ordering stops the whole batch. V1 default is reject the whole malformed batch, eliminating accidental partial effects.

Text deltas may be rendered immediately and marked provisional until exchange completion. A cutoff cannot silently concatenate a new attempt into an apparently continuous response. If retry is allowed before any visible output, use a fresh exchange ID; after visible partial text, emit an explicit interrupted-response boundary. No automatic retry may execute already dispatched calls again.

Provider refusal is a normal typed response with `stop_reason:refusal`; it is not a permission decision. Length cutoff without complete calls cannot run those calls. An output cutoff after complete text preserves partial answer and `task_status:partial`; next-exchange continuation is bounded by the same limits and explicitly labelled.

### 6.4 Dispatch ordering

Prepare the complete call list, validate tool schemas and classify resource reservations. Pure independent reads may run concurrently up to the configured limit. Unknown footprints and commands that can mutate are exclusive reservations. Conflicting calls preserve model order. Policy checks occur before dispatch and immediately before effect admission; a cached allow decision cannot survive path/preimage/policy changes.

Results may finish in any order for progress display; the canonical conversation projection orders them by the original assistant call list. Persist event arrival order honestly, retaining each original index. All results in a completed group appear before the next provider request. A cancelled unstarted call receives a terminal cancellation result without creating an effect ID.

## 7. Effect admission, idempotency and recovery

Every operation capable of a process launch, filesystem mutation, network side effect, hook or MCP effect receives an internal `effect_id`. Tool call IDs are not idempotency keys across model requests. The effect record binds the canonical arguments hash, cwd/project, policy snapshot/decision, checkpoint/preimage refs and originating `(turn_id, exchange_id, call_id)`.

```text
PROPOSED → INTENT_DURABLE → STARTED → COMPLETED
                                    ↘ FAILED
                                    ↘ CANCELLED
                                    ↘ OUTCOME_UNKNOWN → RECONCILING
```

`FAILED` is permitted only when failure is known, with an explicit `effects_observed` field; it cannot imply no effects. `CANCELLED` describes acknowledged stop and known partial effect disposition, not rollback. `OUTCOME_UNKNOWN` blocks automatic repetition and later conflicting effects until reconciliation or user-directed disposition.

The write-ahead intent boundary does not make arbitrary external actions exactly once. A crash between execution and result persistence can leave a changed file or live process with only an intent recorded. Recovery must inspect content/journal/process identity and classify outcome. It may finish a deterministic file transaction under its stored journal after revalidation; it may not replay a shell command just because its last event is `started`.

Repeating a runtime request with the same internal effect ID and identical arguments returns its recorded state. Reusing that ID with different arguments is `effect_id_conflict`. The model cannot supply an internal effect ID. A new call proposing the same command is a new effect requiring policy and duplicate-risk evaluation; textual similarity is not proof of a safe retry.

For arbitrary network effects started from shell or MCP, local process termination cannot establish whether the remote action happened. Record `external_effects:possible` and require reconciliation/manual disposition if outcome is relevant to further work. The CLI must say what is known, what remains unknown and which operation is blocked; it must not ask the model to guess success.

## 8. Cancellation, signals and steering

First SIGINT during a turn durably requests user cancellation when storage permits, immediately closes inference/tool admission, cancels approval waits, requests provider abort and terminates owned foreground/background descendant groups. Proposed default graceful retirement is 2 seconds, then a qualified platform force-stop path with 1 additional second. Time spent flushing evidence is bounded separately; termination must not wait indefinitely behind full stdout/provider queues.

Second SIGINT escalates retirement immediately. It does not destroy the journal or checkpoint data. At an idle interactive prompt, first SIGINT clears the current input; a subsequent input-level exit is explicit. A handled SIGTERM requests the same graceful interrupted shutdown and exit 130; an unhandled OS termination may be reported by the invoking shell under its own signal-exit convention and creates no fabricated product terminal receipt. SIGKILL cannot be handled and is recovered as incomplete invocation on restart.

Process ownership requires a logical process ID plus launch nonce, OS boot identity and start identity. Raw PID alone is never sufficient on restart. Signal only processes whose identity and containment match the owned group. If descendants escape tracking or a backend cannot prove termination, report `children_unresolved` and effect uncertainty; never signal an unrelated process with a reused PID.

A provider may ignore cancellation and continue billing. Close its owned connection, record whether abort was acknowledged, and reject late deltas for state advancement. Usage arriving late may append a bounded accounting correction tied to the original exchange, but cannot change `INTERRUPTED` to `COMPLETED` or dispatch calls.

Steering carries `instruction_id`, accepted sequence, source actor, target turn and content hash. Persist before acknowledgement. Apply once at a safe boundary; if an active effect cannot be safely pre-empted, acknowledge receipt while clearly showing `pending`, then process cancellation first if requested. Ordinary steering narrows or updates task intent within user authority; it does not silently cancel already admitted effects. A direct instruction to stop takes precedence over queued ordinary steering.

## 9. Provider failure, retry and substitution policy

| Failure class | Automatic action allowed | Forbidden inference |
|---|---|---|
| Local validation/auth/config | Fail with actionable redacted code | Retry every credential or discover unrelated secrets. |
| Rate limit or transient connect failure before visible response | Up to configured exchange retries, respecting bounded retry hints and deadline | Unlimited exponential retries or ignoring user cancellation. |
| Connection lost after request send | Record inference outcome/cost unknown; retry only inference under enabled policy and distinct exchange identity | Claim request unbilled or reuse uncertain tool outcomes. |
| Stream cutoff after visible response | Preserve partial output, explicit boundary; continue/retry only when policy permits and no unresolved effects | Join duplicate prose as a single completed response. |
| Invalid/duplicate tool payload | Reject batch with protocol evidence | Repair arguments invisibly and execute guessed intent. |
| Tool failure with known result | Return result to model for bounded task reasoning | Treat command exit 0 as proof of requested behaviour. |
| Required sandbox/persistence backend unavailable | Stop admission, fail closed | Fall back silently to an unrestricted subprocess or volatile state. |

Default provider retries: at most 2 additional inference attempts per exchange decision, within the turn timeout; both attempted and failed exchanges count toward turn budgets. Backoff starts at 0.5 seconds, caps at 8 seconds, honours smaller remaining deadlines, and records jitter seed or actual delay for replay. A provider retry hint exceeding remaining budget produces budget exhaustion rather than sleeping past cancellation.

Fallback selection is disabled unless the effective user-trusted configuration lists allowed aliases and enables substitution. Before the first fallback request, persist `model.substitution` with requested/selected aliases, known identities and reason; surface it in text and JSONL. Fallback must preserve required tools/context/permissions and cannot silently lower required capability. Actual served model is `null` if the provider cannot attest it. Never infer it from the requested alias or a vendor brand string.

## 10. Resource and accounting limits

The following are proposed v1 defaults, not measured runtime capacity. Effective values and their provenance appear in `invocation.started`. A user/task may reduce them; increases must remain within declared policy. These limits bound product work, not this repository's separate 30-minute implementation-task workflow.

| Limit | Default / validation / exhaustion |
|---|---|
| Model exchanges per user turn | 100; integer 1–1000; count every sent request, including retry/fallback. |
| Tool calls per exchange | 32; integer 1–128; oversized response rejected before any call runs. |
| Concurrent tool calls | 8; integer 1–32; conflicting reservations still serialize. |
| Turn wall duration | 600 seconds headless; 1800 seconds interactive; positive ≤86400; monotonic deadline. |
| Provider first response | 60 seconds; adapter may declare stricter bound; unknown provider startup is bounded by turn deadline. |
| Tool argument object | 256 KiB UTF-8 and JSON depth ≤32; reject oversize before dispatch. |
| Prompt input | 1 MiB UTF-8; oversize fails invocation, not silent truncation. |
| Public delta record | ≤64 KiB UTF-8; larger text is chunked at valid character boundaries. |
| Tool inline result | 32 KiB UTF-8; complete redacted content retained under `raw_ref` within artifact limits. |
| Event record | 1 MiB; references hold larger payloads. |
| Repair cycles | At most two task-scoped repair iterations after initial verification; attempts and commands recorded. |
| Cost | Optional decimal USD cap; no cap implied by absence. Unknown price/usage means budget enforcement is incomplete and visible. |
| Token budget | Optional explicit cumulative cap; estimated and provider-reported counts never silently mixed. |

Exchange usage updates are either deltas or snapshots declared by the adapter. Deduplicate observations by exchange/update identity. A final total replaces the matching provisional snapshot; it is not added to it. Cache tokens retain the provider's inclusive/exclusive semantics in metadata, avoiding double counting. If any required component is unknown, report known subtotal and `complete:false`; do not print the subtotal as total billed cost. Prices require an explicitly versioned configured price source; runtime must not claim current public prices from a static table.

Before issuing a request, reserve its configured maximum output allowance where token/cost limits are enforceable. When exact input cost is unknown, report the estimate and uncertainty. Hard billing ceilings need upstream enforcement; the local cap is an admission policy with unavoidable in-flight exposure, and the output must distinguish the two.

## 11. Event and rendering contract

Durable and public session events share this minimum envelope:

```json
{"schema_version":1,"event_id":"evt_<uuid>","session_id":"ses_<uuid>","seq":17,"turn_id":"trn_<uuid>","type":"tool.result","occurred_at":"2026-10-27T12:00:00.000000Z","payload":{}}
```

`seq` is contiguous within one session's authoritative journal; stream subscribers may see gaps because they attach mid-session or filter event types. They must retrieve/reconcile by exact sequence and never assume a filtered stdout subset is the full journal. Timestamp is observation metadata; sequence determines order. `turn_id` is null only for session/invocation-level events. Event IDs are stable across replay; timestamps and payloads cannot be regenerated under an existing ID.

| Event family | Required payload facts |
|---|---|
| `invocation.started`, `session.resumed` | Invocation/project/config/policy identities, permissions, model alias, effective limits, recovery dispositions. |
| `turn.started` | Original user message ref, task intent, active instruction/context snapshot IDs. |
| `model.requested`, `model.delta`, `model.completed` | Exchange identity, provider/request model identities where known, delta subtype, completion reason and usage completeness. |
| `model.failed`, `model.substitution` | Stable error or substitution facts, retry decision, preceding exchange relation. |
| `tool.proposed`, `tool.started`, `tool.result` | Exchange/call original index, canonical name, redacted arguments/digest, effect ID if admitted, actual status/raw ref. |
| `permission.requested`, `permission.decided` | Decision ID, exact effect plan/policy hash, authenticated input provenance and decision scope. |
| `context.compacted`, `instructions.changed` | Immutable previous/new projection identities, source range/digests and budget facts. |
| `turn.cancel_requested`, `process.observed` | Cause, ownership identity, signal/observations, unresolved descendants. |
| `turn.completed` | Terminal state, `task_status`, exit mapping, actual verification refs, effects unresolved, usage and limitations. |
| `session.recovery_required`, `invocation.closed` | Recovery reasons, unresolved effects, cleanup disposition and final sequence. |

Render stdout/stderr from these facts. Escape terminal control sequences from repository/model/tool text; preserve normal newline/tab deliberately. Headless JSONL encodes control characters as JSON escapes. Render approval choices from the authenticated decision model, never by printing an untrusted tool string as an actionable prompt.

## 12. Deterministic acceptance scenarios

Local invariants refine the pack's [global invariants](./architecture.md): R-01/R-08/R-10 map to INV-02/07/12; R-02/R-05/R-11 to INV-01/03; R-03/R-04 to INV-02/08; R-06/R-09 to INV-08/09/13; R-07 to INV-07/08; R-12 to INV-12/13. These local IDs do not supersede the global definitions. A disagreement is a semantic blocker, not permission to select a weaker clause.

All scenarios below are **UNRUN**. The owning task must register exact assertions, source/input hashes and a single focused gate. Real adapter serialization may be checked with captured protocol bytes; that evidence cannot qualify a model's coding ability or substitute for the real-provider journey.

| ID | Setup / action | Required observable oracle |
|---|---|---|
| RC-001 | Invoke installed wheel outside original checkout with gateway absent and one authorised configured provider. | Real model answer, provider receipt and durable terminal event; no old-package module origin or local listener. |
| RC-002 | Remove credential, use unknown model alias, invalid cwd and NUL-containing config separately. | Nonzero typed failure before network/tool effect; credential bytes absent from all artifacts. |
| RC-003 | Stream valid text and fragmented two-tool response; reorder independent tool completion. | Provisional text ordered; tools run only after valid completion; original call IDs/order retained in model transcript. |
| RC-004 | Cut off stream midway through tool JSON; send duplicate IDs or sanitized name collision. | Zero batch effects; explicit protocol error and retrievable redacted wire evidence. |
| RC-005 | Deny a mutation after a successful read; run headless without approval route. | Read may remain observed; refused effect has no started record or file change; exit 3 if blocking. |
| RC-006 | Interrupt before dispatch, during provider stream, during a process with descendants and during approval. | Admission closes immediately; one terminal turn, correct cancelled-before-run pairing, owned children stopped or explicitly unknown. |
| RC-007 | Deliver provider completion after cancellation; deliver late usage separately. | No new effect or success transition; optional accounting correction preserves interrupted state. |
| RC-008 | Crash after effect intent, after spawn, after file change and before result append. | Restart reconciles exact effect identity; no automatic shell replay; unresolved effects block conflicting new work. |
| RC-009 | Exceed exchange, duration, tool count, argument and output bounds independently. | Typed budget/validation result; exact boundary counts and no hidden truncation or runaway wait. |
| RC-010 | Provider sends provisional then final usage, unknown cache fields and absent price. | No double count; null/completeness retained; known subtotal not labelled billed total. |
| RC-011 | Enable configured fallback, then disable it; use incompatible fallback capability. | Enabled substitution announced before request; disabled/incompatible paths never silently switch. |
| RC-012 | Pipe JSONL to consumer that closes during a tool-capable turn. | No subsequent effect admission after disconnect detection; journal contains cancellation/uncertainty disposition. |
| RC-013 | Real PTY sends slash commands, resizes and prints malicious ANSI/tool approval-looking output. | Commands affect shared runtime; terminal remains readable; output cannot answer approval. |
| RC-014 | Send two steering IDs twice with ordinary instruction before stop; restart after acknowledgement. | Applied once in accepted order or explicit pending/rejected state; stop pre-empts ordinary work. |
| RC-015 | Change project config to request new endpoint/credential/unrestricted mode. | Trust gate or restrictive refusal before use; no repository-controlled authority escalation. |
| RC-016 | Real installed agent repairs seeded failure using actual read/edit/exec and reports failed optional second check. | Independent first check passes; second failure remains visible; final task status follows declared acceptance, not fluent summary. |

Passing document parsing, source inspection or transcript replay is not RC-001/RC-016 runtime evidence. The release record must report each implemented adapter/platform variant separately, including blocked and unsupported paths.

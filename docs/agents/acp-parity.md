# ACP provider contracts and limitations

Gideon supports external agent runtimes through Agent Client Protocol (ACP) adapters. A shared protocol does not guarantee identical tools, permission requests, context accounting or lifecycle behavior. This document describes current source contracts and the recorded permission-coverage registry; it does not certify every installed CLI version.

## Runtime boundary

The [connection pool](../../runtime/gideon/integrations/acp/connection_pool.py) manages ACP connections. The [transport](../../runtime/gideon/integrations/acp/transport.py) handles protocol transport and child processes. Bundled providers register commands, dialects and optional declarations through [the registration helper](../../runtime/gideon/integrations/acp_bundles/_register.py).

The external CLI remains a separate runtime. Its authentication, model execution and tools can differ from the native agent. Executable readiness alone does not establish successful session authentication, task execution, tool reporting or shutdown.

## Permission authority

[Permission policy](../../runtime/gideon/integrations/acp/permission_authority.py) normalizes requested modes. Recognized restrictive modes pass through. Missing or unknown modes become the host authority mode. Recognized automatic approval modes are allowed only for unattended sessions; attended sessions receive the restrictive mode instead.

This controls forwarded modes and permission frames the adapter receives. It cannot create a permission frame for an operation the external runtime never exposes. Accepted registry entries remain documented limitations, not host-gated operations. Unaccepted entries retain the policy's warning posture.

App installation consent, current app capabilities and host approval policy remain separate constraints. Provider configuration and displayed tool names do not confer authorization.

## Session declarations

Registration can declare JSON-compatible session metadata and whether a provider compacts its own context. [Validation](../../runtime/gideon/integrations/acp/options.py) rejects non-JSON metadata and non-boolean compaction flags. The pool forwards these declarations to session implementations.

A compaction declaration describes lifecycle responsibility, not evidence of a successful compaction. Missing token usage or context measurements remain unknown. Provider-specific options require support from both the registered adapter and the actual CLI.

## The not-gateable residual, per provider

The following block is generated from the source registry. Its observations, dates and status values describe recorded coverage, not a fresh test of the reader's installed binaries.

<!-- BEGIN GENERATED: not-gateable-registry (tooling/scripts/render_acp_parity_residual.py) -->
<!-- Regenerate with: python tooling/scripts/render_acp_parity_residual.py -->

- **`claude-code`** — 2 declared residual entries.
  - Measurement: Recorded claude-code coverage contains seven persisted ungated events across four sessions and two tool titles. These events do not support a claim of universal host permission coverage.
  - `Terminal`
    - Reason: Some shell calls execute without a session/request_permission event. Host deny-list, task-mode and blocking pre-tool controls that depend on that event cannot gate these calls. This remains an unaccepted limitation.
    - Observation: Recorded execute-kind ungated events include the Terminal title and report that no permission request was received for the tool call.
    - State: measured, NOT accepted — the host cannot gate it and nobody blessed it, so it stays loud
  - `Read File`
    - Reason: Some file reads execute without a session/request_permission event. The host cannot present a decision for those reads. This remains an unaccepted limitation; effective SAFE risk does not abort the turn.
    - Observation: Read File is one of the two tool titles in the seven recorded ungated claude-code events.
    - State: measured, NOT accepted — the host cannot gate it and nobody blessed it, so it stays loud
- **`codex`** — 1 declared residual entry.
  - Measurement: Recorded codex coverage includes four ungated events: a file read, a workspace write, a write outside the workspace and a network call. These events do not support a claim of universal host permission coverage.
  - `codex-native`
    - Reason: In default permission mode, native file, shell and network operations can execute before the host receives a permission request. A completed write outside the workspace without a host decision remains an unaccepted limitation.
    - Observation: Recorded operations include a read, a workspace write, a completed write outside the workspace and a network call without host permission requests. Other calls in the same recording did request permission, including a push operation that the deny-list refused.
    - State: measured, NOT accepted — the host cannot gate it and nobody blessed it, so it stays loud
- **`kiro-cli`** — 2 declared residual entries.
  - Measurement: Recorded kiro-cli coverage includes a 2026-08-18 turn with six tool calls: one permission request and five calls without a permission request (four task-list calls and one file read).
  - `todo_list`
    - Reason: The task-list tool emits a tool-call event without a session/request_permission event. Host controls that depend on a permission request cannot gate this operation.
    - Observation: In a recorded thirteen-call turn, seven task-list calls executed without permission requests and were classified as destructive by the host. File-read, file-write and deletion operations in those turns did request permission.
    - State: measured, accepted — a documented limitation; the host labels it and stays quiet
  - `fs_read`
    - Reason: Some file reads execute without a session/request_permission event, even when a file write in the same turn requests permission. The host cannot present a decision for those reads.
    - Observation: A recorded 2026-08-18 turn requested permission for a file write but not for a file read. This accepted read limitation is labelled without aborting the turn.
    - State: measured, accepted — a documented limitation; the host labels it and stays quiet
<!-- END GENERATED: not-gateable-registry -->

## Checking a configured provider

Record the adapter, executable version, model and relevant configuration. Exercise actual session creation, streaming completion, exposed permission requests, interruption, cleanup and reconnection. Verify supported session options, context usage and compaction independently.

Distinguish a denied host operation from an operation that never reaches the host. Record missing events and unavailable capabilities explicitly. Native-agent tests do not qualify external ACP runtimes, and successful CLI startup does not qualify every tool.

The generated registry can be checked without executing a provider:

```bash
python3 tooling/scripts/render_acp_parity_residual.py --check
```

See [provider boundaries](../architecture/PROVIDER_BOUNDARY.md) and [security boundaries](../architecture/SECURITY.md) for surrounding host contracts.

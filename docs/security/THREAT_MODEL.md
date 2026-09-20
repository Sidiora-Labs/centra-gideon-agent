# Threat model

Gideon runs an autonomous agent on the owner's own machine. This document makes
its security posture externally checkable. It lists the trust boundaries Gideon
defends, the controls that guard each one, the code behind them, a mapping to the
OWASP Agentic Security (ASI) Top 10, and an honest statement of what Gideon does
not defend against.

Every "enforced" claim below cites a module under `Gideon/src/gideon/`. The
architecture narrative behind these controls is in
[`docs/architecture/SECURITY.md`](../architecture/SECURITY.md). The current
limitations are in [`LIMITATIONS.md`](LIMITATIONS.md).

**Verified against:** `main` at the commit that introduced this file. Controls
change over time. If a citation below no longer resolves, treat that row as
unverified and file an issue.

## Trust boundaries

Gideon defends five boundaries. Each one is a place where something less trusted
meets something more trusted, with named controls at the crossing.

### 1. Owner to agent to tools

The owner directs the agent, and the agent invokes tools that act on the machine.
The crossing is gated so the agent cannot act outside the owner's chosen posture:

- **Task modes** (`task_modes.py`) decide which tools may run per session
  (`agent`/`ask`/`plan`/`build`). They are hard-enforced in the native runtime's
  `_guard_and_invoke`, before approval is consulted.
- **Command screening** (`security.py`): a deny list and suspicious-pattern
  watchers (`SUSPICIOUS_BASH_PATTERNS`). The deny list ships as the packaged
  baseline in `baseline_denylist.json`. It is re-asserted and merged with user
  config at read time by `denied_command_patterns()`. See [Baseline denylist
  integrity](#baseline-denylist-integrity-anti-drift-and-anti-llm-tamper-not-anti-owner).
- **OS child sandbox** (`sandbox.py`), with a credential-env denylist so secrets
  never reach a sandboxed child.
- **Trust/YOLO state** (`trust_mode.py`): one process-global auto-approve state,
  config-permanent or TTL'd, with `on_disable` callbacks.

#### Baseline denylist integrity: anti-drift and anti-LLM-tamper, not anti-owner

The always-on bash denied-command patterns ship as packaged data
(`gideon/baseline_denylist.json`, shaped `{version, sha256, patterns[]}`). The
digest is verified at import, re-asserted on every `denied_command_patterns()`
read, and re-verified by the `security.baseline_denylist` doctor probe.
`GET /api/security/denied-commands` returns that state as a `baseline` block
(`version`, `sha256`, enforced `count`, `verified`), and the Security page in
Settings renders it beside the read-only pattern list.

What the digest does catch:

- On-disk corruption or a partial write. The module raises at import rather than
  coming up with a shorter, or empty, denylist.
- An edit that changed the patterns but not the `sha256` shipped beside them.
  Same hard failure at import.
- Mutation of the in-memory list inside a running process, such as a stray
  `.clear()`, a monkeypatch, or a `sitecustomize`. The next read heals it from
  the verified snapshot and audits `baseline_denylist_reasserted` with the
  patterns that came back.
- A self-consistent rewrite of the packaged file, with the patterns and the
  digest changed together. The fingerprint is held in memory from import onward,
  so the file is never consulted for content again. The divergence is audited as
  `baseline_denylist_tamper_attempt`, and the verified baseline stays in force.

What it does not do:

- It does not stop the owner. Anyone who can edit the installed package before
  the process starts owns the baseline. A different `patterns[]` with a matching
  `sha256` verifies happily, because at that point it is what shipped.
- It is not a tamper-proof control, and no surface may present it as one. The
  panel says the baseline "matches what shipped", or that the packaged file no
  longer matches. It never says "secure", and it never says "tamper-proof".

The threat this closes is drift inside a running process. The concrete case is an
agent asked to relax its own guardrails: the model can write files and run
commands, so it can reach the list, but it cannot make a shortened list stick,
and it cannot make the shortening silent. The threat it does not close is the
owner reconfiguring their own machine. That is a decision rather than an attack,
the same posture as the auto-approve bullet under [What we deliberately don't
defend against](#what-we-deliberately-dont-defend-against).

### 2. Core to apps

Installed apps extend the gateway but must not reach the owner's full authority:

- **App-scoped tokens** (`dashboard/token_auth.py::generate_token`, with an `app`
  claim) bind a request to that app's declared permissions. TTLs are capped by
  `MAX_SESSION_TTL_SECS`.
- **Reverse-proxy credential stripping**
  (`dashboard/handlers/apps.py::api_app_proxy`): app backends never see the
  owner's cookie or Authorization header. A fresh 1-hour app-scoped token is
  injected instead.
- **Permission middleware** holds in every auth mode, including `none`. In that
  mode, `dashboard/server.py`'s `_dev_user_middleware` re-adopts the app claim
  through `validate_token_with_app`, so an app token only ever narrows reach.

These controls bind app-scoped clients and backends, not frontend bundle code.
App UI executes in the host origin and therefore has the host DOM, session
cookie, and same-origin API reach. That is a documented limitation and an
advisory shown during install, not an isolation boundary.

### 3. Gateway to channels and inbound

Content and requests arriving from outside the owner's trust boundary:

- **Untrusted-content fencing** (`security.py::fence_untrusted`) wraps
  third-party text in `<untrusted_content>` markers with a data-not-instructions
  system note. It is applied to web-search results, inbox content, and
  third-party payloads.
- **Webhook auth** (`dashboard/handlers/hooks.py::_verify_hook_token`): a
  constant-time (`hmac.compare_digest`) token check. With no configured token,
  every request is refused, and denials log to the Security Event Log.
- **Egress chokepoint** (`net/client.py`, plus `net/guard.py` and
  `net/policy.py`): the single outbound-HTTP seam, with named policies layered by
  `net/policy.py::egress_policy_for`.

Inbound MCP and external remote access (fail-closed inbound, fencing at
ingestion) are owned by MCP-READONLY-INBOUND and EXTERNAL-ACCESS. They have not
landed yet, see the ASI07 row.

### 4. Install pipeline to sources

Installable content (apps and skills) from arbitrary sources:

- **Quarantine, scan, consent, install** (`apps/app_manager.py::install`):
  content is staged in quarantine, scanned there, and moved into place only if it
  passes. The scanned bytes are the installed bytes, so there is no
  time-of-check/time-of-use gap.
- **Scanner verdicts** (`supply_chain.py`: `SkillScanner`, `Verdict`): `clean`,
  `warning` (consent required), or `dangerous` (terminal, non-overridable).
  `TrustTier` modulates strictness.

### 5. System to persisted and exported state

Data leaving the running system:

- **Tamper-evident audit** (`sel.py::SecurityEventLog`): HMAC-chained,
  append-only events (caller, operation, outcome).
- **Redacted archive reads** (`security.py`: `redact_credentials`,
  `redact_exfiltration_urls`).
- **Credential-excluding exports** (`portability.py`): `.env`, `sel_hmac.key`,
  and `session_map.json` are on the export exclusion list.
- **Memory privacy** (`session_restrictions.py`): temporary and incognito
  sessions gate memory reads and writes.

## OWASP Agentic Security (ASI) Top 10 mapping

Status legend: **enforced** (a resolvable control gates it), **in progress (plan
N)** (the control is designed, not yet landed), **documented limitation** (a
deliberate, disclosed gap, see [`LIMITATIONS.md`](LIMITATIONS.md)). A row may
claim `enforced` only with a resolvable `file:path` citation.

| ASI category | Control | Code citation (`file:path`) | Status |
|---|---|---|---|
| **ASI01** Agent goal / instruction manipulation | Untrusted-content fencing, approval modes, and data-not-instructions framing on recalled memory | `security.py::fence_untrusted`; `dashboard/handlers/memory.py` (recall framing) | enforced |
| **ASI02** Tool misuse | Command deny/suspicious patterns, task-mode gating, OS child sandbox | `security.py` (`BUILTIN_DENIED_COMMAND_PATTERNS`, `SUSPICIOUS_BASH_PATTERNS`); `task_modes.py`; `sandbox.py` | enforced |
| **ASI03** Identity & privilege abuse | App-scoped tokens, reverse-proxy credential stripping, permission middleware (holds even in `none` mode) | `dashboard/handlers/apps.py::api_app_proxy`; `dashboard/token_auth.py`; `dashboard/server.py` (`_dev_user_middleware`) | enforced |
| **ASI04** Supply-chain & dependency risk | Quarantine, scan, consent, install; `dangerous` verdict terminal; scanned tree equals installed tree | `apps/app_manager.py::install`; `supply_chain.py` (`SkillScanner`, `Verdict`) | enforced |
| **ASI05** Unauthorized code execution | Command screening, OS sandbox, credential-env denylist | `security.py`; `sandbox.py` | enforced |
| **ASI06** Memory & context poisoning | Fenced recall, propose-only (never live-write) learning, temporary/incognito session modes | `dashboard/handlers/memory.py`; `after_turn_review.py` (propose-only queue); `session_restrictions.py` | enforced |
| **ASI07** Insecure inter-agent / inbound comms | Fail-closed inbound surface and fencing at ingestion | *(owned by MCP-READONLY-INBOUND and EXTERNAL-ACCESS)* | in progress (plans 41, 24) |
| **ASI08** Cascading failures / denial-of-wallet | Circuit breakers, budgets, spend caps | *(owned by AUTONOMY-GUARDRAILS)* | in progress (plan 9) |
| **ASI09** Trust exploitation / social engineering | Approval surfaces, expiring YOLO with `on_disable` callbacks, consent-gated installs | `trust_mode.py`; `apps/app_manager.py::install` | enforced |
| **ASI10** Rogue / runaway agents | Tamper-evident audit log, and YOLO kill/disable (auto-approve is revocable and fires disable callbacks) | `sel.py::SecurityEventLog`; `trust_mode.py` (`on_disable`) | enforced (incident-flag on breaker trip: in progress, plan 9) |

## What we deliberately don't defend against

Gideon is a single-owner, self-hosted tool. Some things are out of scope by
design, not by omission. Stating them keeps the in-scope claims credible.

- **Physical access to the machine.** If someone has your unlocked device, they
  have your agent. Gideon is not a defense against local physical access.
- **A compromised host OS or OS account.** The controls above assume that the
  machine, and the account Gideon runs under, are trustworthy. Root on the box, a
  compromised user account, or malware already on the host are outside the model.
  They sit below the boundaries Gideon defends.
- **The owner's own auto-approve (YOLO) choices.** Gideon lets its owner lower
  their own guardrails. Choosing auto-approve, or running an external ACP agent
  under YOLO (where gating rides system-prompt framing, not rails, see
  [`LIMITATIONS.md`](LIMITATIONS.md)), is an owner decision, not a vulnerability.
- **Tampering with the installed package before startup.** The baseline
  denylist's digest proves the patterns in force are the ones that shipped with
  this process. It cannot prove which patterns were shipped. Whoever can edit the
  installed package before Gideon starts sets the baseline. That control is
  anti-drift and anti-LLM-tamper, not anti-owner. See [Baseline denylist
  integrity](#baseline-denylist-integrity-anti-drift-and-anti-llm-tamper-not-anti-owner).
- **An app's own outbound network traffic.** The `network` app permission is
  declaration-only (disclosed at install consent), not a gateway-enforced
  boundary. An app backend is its own OS process with its own network stack. See
  [`LIMITATIONS.md`](LIMITATIONS.md). What is enforced is the supply-chain gate on
  what you install, plus the app's gateway-mediated (`api`) reach.

Each of these has a rationale above, and none is an accident. Gaps discovered
while maintaining this document are routed to the security-hardening track as
candidates. They are never patched inline in a docs change.

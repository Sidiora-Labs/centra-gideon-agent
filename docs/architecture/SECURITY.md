# Security architecture

This document covers Gideon’s single-owner OSS runtime, which can act on its host
and configured services. Gideon also offers a hosted service; hosted account and
control-plane security are outside this repository-level description.
Controls apply at different seams: HTTP admission, tool invocation, app lifecycle,
work lineage, network dispatch, memory scope and persistence. Paths below are relative
to the repository root; they identify implementation, not a claim of universal coverage.

## Auth modes

`runtime/gideon/security/auth/modes.py` supports **`local_token`** (default) and
**`none`**. Unsupported spellings, including `api_key` and `oauth2`, raise during
configuration parsing; they do not silently select another mode. None mode forces the
bind to loopback through `effective_bind`.

`runtime/gideon/interfaces/dashboard/token_auth.py` implements gateway token admission,
owner login/session behavior and app identity adoption. Owner login credentials and
second-factor helpers live under `runtime/gideon/security/auth`. These are single-owner
authentication mechanisms in the OSS runtime. Hosted service account and tenant
controls are a separate layer, not an absent Gideon offering. An opt-in
local-network bypass is a separate exposure choice. It must not be assumed safe behind
a public reverse proxy.

App-scoped identity must survive every supported auth mode. The dashboard server's
none-mode middleware still validates app credentials and adopts their narrowing claim.
App access also consults `extensions/apps/permissions.py`, which retains owner-only
operation rules even when an app lists a broad API prefix.

## Tokens, proxying and inbound surfaces

Dashboard tokens grant real gateway access. Do not put tokenized URLs in screenshots,
logs or public reports. Token TTL, nonce revocation, origin and CSRF controls are
implemented in the dashboard auth modules.

The app reverse proxy strips owner cookies and authorization, supplies app-scoped
credentials, and signs forwarded requests. The backend SDK signature verifier is
required to reject direct unsigned requests. See [App platform](APP_PLATFORM.md).

The dedicated agent webhook verifies its configured hook token separately.
`runtime/gideon/integrations/inbound` contains MCP, capture, compatible chat and A2A
surfaces. They have separate admission, tokens/client permissions and master/per-surface
switches. `inbound/gate.py` checks current enablement and incident state; unreadable
admission state refuses work. Disabled or unmounted surfaces must not be treated as
available merely because their modules exist. These are configured local gateway
integrations, not a default public service.

## Tool authority and approvals

`runtime/gideon/engine/task_modes.py` classifies actual invocations for task-mode
admission. Native runtime dispatch intersects current agent tool/skill selections,
app work tiers, safety profile and approval policy. Profile grant widening uses an
owner-reviewed offered change; a metadata write does not grant capability.

`runtime/gideon/security/approval_answer.py` distinguishes the actual approving
principal, and `approval_grants.py` stores applicable reviewed grants. Approval of
one call is not automatically standing authorization. Caller-supplied labels, headers
or session keys cannot substitute for an authenticated principal.

ACP coverage depends on the provider's permission events. The host normalizes permission
modes through `integrations/acp/permission_authority.py` and handles reported calls.
An external CLI's unreported operations remain outside that protocol gate. Recognized
unattended auto-approve modes and measured provider residuals need explicit consideration;
see [Limitations](../security/LIMITATIONS.md).

## Work origin, descendants and memory

`runtime/gideon/security/session_credentials.py` binds live work to actual sessions and
execution lineage. `durable_work.py` validates accepted durable workflow and trigger
origins. Original initiator and effective narrowed app actor are distinct, so delegation
must preserve attribution while intersecting capabilities. Mutable run metadata or an
app-shaped trigger name alone is not an authority receipt.

App tier enforcement lives in `runtime/gideon/extensions/apps/app_work.py`: current enabled
permissions can narrow an already-held grant, and descendants cannot widen it. Text work
receives task text without tools or persistent-memory context. Read work still consults
actual invocation classification. Tool-capable work retains approval and scope checks.

Memory reach is resolved from verified work, privacy mode and current app consent.
Hypermid issues typed scopes under `runtime/gideon/hypermid`, including app namespaces
and private-work resources. Temporary forbids persistent reads and writes; Incognito
forbids writes. Private workflows validate the live receipt before execution and retire
private resources through actual terminal lifecycle. They do not gain a persistent scope
by changing a mode label or supplying a workspace identifier.

## Commands and child processes

`runtime/gideon/security/security.py` supplies command screening, protected-path checks,
redaction and untrusted-content fencing. The packaged `baseline_denylist.json` has a
verified digest; runtime reads reassert the baseline while incorporating configured
restrictions. This detects certain drift and tamper attempts. It cannot authenticate an
installation an attacker replaced before startup.

`security/sandbox.py` builds child environments from an allowlist, with sensitive-name
restrictions and explicitly configured passthrough. Credential-path hiding depends on
OS and sandbox mode. It is not a general filesystem-write jail or resource limiter.

Command egress narrowed to a no-network posture must use an OS no-network wrapper,
or be refused before spawn if that cannot be enforced. `wrap_program_argv` is a separate
path for declared programs; `network=False` also requires actual enforcement. Other
sandbox-provider specifications must be evaluated against the provider implementation;
no whole-spec guarantee follows from the existence of a wrapper interface.

## HTTP egress

`runtime/gideon/security/net/{client,guard,policy}.py` provides guarded HTTP dispatch,
destination checks, named policies and run-profile narrowing. Some policies are exclusive
allowlists: an empty allowed set means no destination. Checks of private and metadata
addresses and resolved destinations belong to this guarded path.

This is the gateway-mediated HTTP seam, not an interception layer for every socket opened
by an imported library, app or external browser. Library/download hooks and child command
policy have their own consumers. A real user browser controls its own DNS/socket behavior;
preflight checks do not give it the same resolved-IP pinning as the native HTTP client.

## Governance, budgets and incidents

`runtime/gideon/security/guardrails/ceiling.py` composes the operator ceiling with run
profiles: the tighter policy wins. Ceiling files and matcher/ordinal registries are
validated; run policy cannot define a looser scale for itself. The owner controls the
host and can replace files before startup, so this is not OS immutability.

The guardrails package includes provider and loop breakers, budgets, model-call admission,
autonomy routing and incident state. Their consumers determine actual coverage; a spend
estimate is not a bill from a provider. Incident admission suspends relevant unattended
and inbound work. Existing user approvals do not override an active refusal at another
seam.

## Install and dependency boundary

`runtime/gideon/security/supply_chain.py` scans staged app/skill content. Dangerous
verdicts are terminal; warning verdicts need consent. App review compares the offered
staged digest and execution disclosure. A scanner result does not prove arbitrary code
safe or make it isolated.

App Python dependencies use the shared writable `<GIDEON_HOME>/app-python` prefix and
base-environment constraints, not direct installation into the gateway's virtualenv.
In-process providers still share one interpreter. UI bundles run in the host origin.
App `network` is declaration-only. See [Limitations](../security/LIMITATIONS.md).

## Audit and portability

`runtime/gideon/security/sel.py` stores HMAC-chained security events. This is tamper
evidence under the key's trust boundary, not protection against someone who controls
the host and key. Redaction and write-only credential API contracts are separate controls.
`runtime/gideon/operations/durability` defines export inventory, archive handling and
restore plans. Review the selected domain and actual archive policy; exports are not a
mechanism for transferring live credential authority.

For trust assumptions and exclusions, see [Threat model](../security/THREAT_MODEL.md).
Report vulnerabilities using the repository [security policy](../../SECURITY.md).

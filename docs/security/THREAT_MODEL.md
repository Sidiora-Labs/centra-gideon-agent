# Threat model

This threat model covers Gideon’s single-owner, self-hosted OSS runtime. Gideon
also offers a hosted service; its control plane is outside this document.
The runtime can read files, invoke tools,
run programs and contact configured services. The owner trusts the operating system,
the account running Gideon and the installed distribution. This document identifies
boundaries and source controls; it does not certify every integration or deployment.

Paths are relative to the repository root. Read this with
[Security architecture](../architecture/SECURITY.md) and [Limitations](LIMITATIONS.md).

## Owner, agent and tools

The owner supplies intent; model output is not itself permission. Native dispatch applies
task-mode classification, current agent grants, app work ceilings, safety policy and
applicable approval before tool execution. Relevant source includes:

- `runtime/gideon/engine/task_modes.py` and `engine/agents/native/runtime.py`;
- `runtime/gideon/security/approval_answer.py`, `approval_grants.py` and `owner_grants.py`;
- `runtime/gideon/security/guardrails` for operator ceilings, budgets, model-call
  admission, breakers and incident state;
- `runtime/gideon/security/security.py` and `sandbox.py` for command screening,
  child environments, protected paths and available OS wrapping.

A one-call approval does not imply a standing grant. Trust/auto-approve changes review
posture; it does not bypass a different scope or task-mode refusal. External ACP agents
only expose operations their protocol reports, so native and external execution must
not be described as having identical enforcement coverage.

### Baseline denylist integrity

The packaged `runtime/gideon/security/baseline_denylist.json` carries a digest and pattern
set. Runtime checks detect corrupt or mismatched packaged data and reassert the verified
baseline against in-process drift. Configured restrictions are composed with it.

This is anti-drift and tamper evidence within an already-trusted installation. A person
who can rewrite the package and its digest before startup controls the baseline. It is
not tamper-proof, anti-owner protection or a replacement for OS account security.

## Gateway and apps

App-scoped tokens identify authenticated app requests. The permission middleware in
`runtime/gideon/extensions/apps/permissions.py` consults installed enabled state and
consent, and retains owner-only operation rules. The backend reverse proxy strips owner
credentials and supplies app-scoped credentials and a signed request. A backend must
use the SDK signature verifier to reject direct unsigned requests.

App-origin agent work additionally carries a current declared text/read/tools ceiling.
Native work credentials preserve original origin and effective app actor; child work
intersects, rather than broadens, the parent's scope. App memory requires current consent
and a native authorized scope, not a caller-supplied namespace.

These gates constrain cooperating app-scoped API and agent consumers. They do not isolate
arbitrary in-process Python, third-party dependencies or host-origin frontend code.
The app `network` declaration is advisory. See [Limitations](LIMITATIONS.md).

## External input and configured inbound services

Channel messages, fetched pages, imported documents, MCP results and recalled records
can contain hostile instructions. Untrusted-content fencing marks data for the model;
it does not prove that the model will never follow malicious content. Actual execution
still needs independent tool and scope checks.

`runtime/gideon/integrations/inbound` implements configured MCP, compatible chat,
capture and A2A surfaces. Admission checks master/per-surface enablement, configured
authentication/client scope and incident policy. Disabled services are not default public
endpoints. Dedicated webhooks use their own configured token verification.

`runtime/gideon/security/net` guards gateway-mediated HTTP destinations and run egress
policy. Restricted child commands use separate OS enforcement. Neither path universally
intercepts sockets opened by arbitrary app code or an external browser.

## Sources, install review and dependencies

Apps and skills from outside the installed distribution cross the supply-chain boundary.
`runtime/gideon/security/supply_chain.py` and the respective installers stage and scan
content, refuse dangerous verdicts and require consent for warnings. App preview and
commit compare the staged bundle digest and declared execution review.

A content scan and a signature establish different facts. Neither is proof of safe
behavior. Dependencies may execute installation code and imported code. App packages
are resolved into the shared writable home prefix with base-package constraints; they
remain shared by in-process providers. Install only code appropriate to that authority.

## Persisted state, privacy and export

`runtime/gideon/security/sel.py` provides HMAC-chained audit events. Tamper evidence
assumes the verification key and expected state remain trustworthy. Credential read
contracts and redaction are separate; do not publish raw runtime state or tokenized URLs.

Temporary work blocks persistent memory reads and writes; Incognito blocks writes while
allowing otherwise authorized reads. `security/session_credentials.py`, durable origin
validation and Hypermid native scope issuers govern actual work reach. Private workflow
resources require a live receipt and terminal cleanup. Provider-side logging and storage
are outside those local privacy controls.

`runtime/gideon/operations/durability` defines the archive inventory and restore behavior.
An export is not a transfer of live credentials or execution grants. Validate the actual
archive and selected domains before moving it to another machine.

## Risk categories and source controls

The following is a navigation map, not an assertion that every attack in a category is
prevented or that an external security standard has been certified.

| Risk | Relevant controls | Material limit |
|---|---|---|
| Instruction manipulation | Content fencing, task modes, approval and grants | Model compliance with fencing is not guaranteed |
| Tool misuse and code execution | Invocation classification, command checks, child wrapping | No general sandbox for in-process app code |
| Identity and privilege abuse | Authenticated principals, app middleware, bound/durable work origins | Host compromise remains out of scope |
| Supply-chain compromise | Staging, signatures, scans and offered digest review | Reviewed code and dependencies can still be malicious |
| Memory/context poisoning | Scoped authorization, privacy modes and capture provenance | Provider data quality and model interpretation still matter |
| Inter-agent/inbound abuse | Dedicated admission, client scopes and descendant attenuation | Coverage depends on the actual configured transport |
| Cascading failures and runaway spend | Model/loop breakers, budgets and incidents | Estimates and configured limits are not provider billing guarantees |
| Trust exploitation | Explicit review, separate one-call/standing grants and revocation | Owners can choose less restrictive posture |
| State tampering | Audit chains, atomic stores and revision checks | A host/key owner can replace trusted state |

## Outside this model

- A compromised operating system, root account, or the owner account's unrestricted file
  access. Local malware or an unlocked machine sits below the application boundary.
- An installed package deliberately replaced before startup by someone who controls it.
- General isolation of trusted-to-run app Python, dependencies or browser UI code.
- Provider-side confidentiality, retention or billing beyond the provider's own contract.
- Identical tool visibility from every external agent runtime and platform.

Remote exposure, owner login, network bypasses, app installation and auto-approve are
operator choices with real authority consequences. Configure them for the actual host
and integration rather than assuming a source feature implies a safe public deployment.

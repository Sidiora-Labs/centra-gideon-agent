# Security review scope

This document identifies useful review targets in the current runtime. It is a review
outline, not evidence that an independent review was commissioned, completed, or passed.
Use [SECURITY.md](../../SECURITY.md) for reporting and supported-version policy, and
compare findings with the [threat model](THREAT_MODEL.md) and
[known limitations](LIMITATIONS.md).

Record the actual revision and configuration under review. Source citations make claims
inspectable; they do not establish that the claimed behavior holds in every deployment.

## High-risk boundaries

| Boundary | Current source entry points | Questions to exercise |
|---|---|---|
| Inbound hooks and scheduled dispatch | `runtime/gideon/interfaces/dashboard/handlers/hooks.py`, `runtime/gideon/automation/triggers/` | Does the hook authenticate before effects? Does actual accepted trigger consent remain bound to the current action revision and grants? |
| App tokens and reverse proxy | `runtime/gideon/interfaces/dashboard/handlers/apps.py`, `runtime/gideon/interfaces/dashboard/token_auth.py`, `runtime/gideon/extensions/apps/permissions.py` | Are owner credentials stripped? Can an app token reach another app or acquire owner authority? Do disabled apps and revoked grants fail closed? |
| Install, update, and content integrity | `runtime/gideon/extensions/apps/app_manager.py`, `runtime/gideon/extensions/skills/marketplace.py`, `runtime/gideon/security/supply_chain.py`, `runtime/gideon/security/signing.py` | Are reviewed/scanned bytes the installed bytes? Are invalid signatures, unsafe paths, symlink races, and terminal verdicts refused before hooks or registration? |
| Network and process egress | `runtime/gideon/security/net/`, `runtime/gideon/security/sandbox.py` | Which consumer is guarded? Do policy intersections narrow correctly? Does an unenforceable process restriction refuse rather than report containment? |
| Inbound protocols and dashboard origins | `runtime/gideon/integrations/inbound/`, `runtime/gideon/interfaces/dashboard/server.py`, `runtime/gideon/interfaces/dashboard/ws.py` | Are flags, peer policy, bearer tokens, rate/concurrency caps, and Origin checks all applied? Do read-only surfaces remain read-only? |
| Work identity and memory reach | `runtime/gideon/security/session_credentials.py`, `runtime/gideon/security/durable_work.py`, `runtime/gideon/hypermid/` | Can supplied metadata replace host proof? Do original and effective actors remain distinct? Are native private/app scopes live, narrowed, and revoked through their actual lifecycle? |

These targets are not a complete inventory. A finding outside the table can still matter
when it crosses a documented boundary.

## Distinguish bypasses from documented limits

The host owner can change installed code and configuration. That is different from an
untrusted request obtaining owner privileges. App Python can execute in the gateway
process, and static scanning does not contain arbitrary runtime behavior. An unguarded
app socket is not automatically proof that a documented process or guarded-HTTP boundary
was bypassed; identify the consumer and the exact promise that failed.

Owner-selected approval posture also has limits: permissive mode does not mean every
baseline, risk, scoped-memory, or app-tier restriction is removed. Test the actual
applicable rule rather than treating any configured auto-approval as either a vulnerability
or a blanket exemption.

## Evidence for a finding

Provide the affected revision, configuration, source symbols, input, observed outcome,
and concrete impact. Prefer a mechanical reproduction and a focused regression test.
Separate a code-reading inference from an observed effect; a response code alone may not
show whether work was accepted, dispatched, or completed.

Use an isolated Gideon home and targets you control. Remove credentials, personal content,
and deployment-identifying data from reports. Follow the project's security reporting
channel for potentially exploitable issues rather than publishing live credentials or
attack instructions against someone else's gateway.

A review report should state its author/format, tested paths, attempted attacks, findings,
and untested coverage. Record unavailable prerequisites and shallow attempts explicitly.
Independent review, maintainer self-review, static inspection, focused tests, and live
platform verification are different forms of evidence; do not label one as another.

Report disagreements and unresolved limitations honestly. Publication timing and
remediation should follow the current security policy and the actual review engagement,
not an unconfirmed schedule in this outline.

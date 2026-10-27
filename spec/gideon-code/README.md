# Gideon Code — extraction and CLI coding specification

**Draft for implementation review. Implementation is not authorised by this pack; every product acceptance scenario is UNRUN.** The programme extracts reusable coding behaviour from the existing Gideon platform into an independently packaged local CLI, proves the extracted consumer, and only then presents an exact pruning manifest for separate approval.

This revision follows the depth of the owner's Hyperfide examples: observable requirements, explicit decisions, typed contracts, recovery state machines, narrowly owned vertical task packets, negative cases and evidence boundaries. It does not import Hyperfide's control plane, cloud infrastructure or historical permissions.

## Reading order

| Document | Purpose |
|---|---|
| [Native specification](spec.kvx) | Sole requirement, task, dependency, ownership and status authority. |
| [Architecture](architecture.md) | Product/source boundaries, versioned producer/consumer contracts and invariants. |
| [Parallel execution](parallel-execution.md) | Vertical packet decomposition, file reservations, scheduling, handoffs and semantic drift controls. |
| [Runtime and CLI](runtime-contract.md) | Command grammar, model construction, turn/tool lifecycle, event/usage semantics and cancellation. |
| [Storage and context](storage-context.md) | Session authority, durability, corruption, instructions, compaction, result retention and recovery. |
| [Tools and security](tools-security.md) | Tool input/output/error contracts, path policy, approvals, sandboxing, patch/edit/exec and rewind. |
| [Delegation and extensions](delegation-extensions.md) | Child identity, worktree custody, merge evidence, MCP, local skills and hooks. |
| [Extraction and cutover](extraction-cutover.md) | Inventory, immutable vectors, package independence, exact deletion approval, promotion and rollback. |
| [Verification and evaluation](verification-evaluation.md) | Scenario catalogue, real-path qualification, fault cases, campaign methodology and release evidence. |
| [Traceability](traceability.md) | Every acceptance clause's packet/journey owner, command and contract source. |

## What completion means

A packet's source, registration or test file is not completion. The packet must have a reachable consumer, satisfy its positive/negative/recovery conditions, execute its one focused gate against the identified source, and emit valid evidence. Its integration owner consumes that receipt without repeating an unchanged successful packet gate. The wave then runs its distinct integrated journey once on the merged candidate.

Parallel eligibility requires satisfied dependencies, compatible contract versions and disjoint exact write reservations. An agent cannot add paths, alter a producer/consumer meaning, remove an assertion, reset a retry budget or redefine a successful outcome on its own. Such changes require a revised packet and affected consumer/verification mapping before work continues.

These controls aim to prevent drift and expose semantic errors; they are not a claim that a written specification can guarantee a defect-free implementation.

## Important boundaries

- The old platform remains until standalone installation, carried contracts and coding journeys are proven against the extracted package with legacy source inaccessible.
- Model capability evidence uses an authorised real provider. Wire fixtures establish protocol behaviour only. Scripted answers and mocked effect success cannot qualify a coding journey.
- Checkpoint failure prevents a protected write. Unknown command/effect outcome is not automatically replayed. Rewind preserves subsequent external changes.
- Packaged feature registration cannot discover executable Python from a repository or skill. A child, MCP server, hook or instruction file cannot increase permission.
- Pruning requires exact current source and manifest approval plus verified archive recovery. This draft authorises no deletion, remote publication or personal-state migration.
- Source budgets and dependency limits are proposed acceptance constraints, not measured claims about the final extracted core.

## Authoring validation

The pack includes a read-only structural validator:

```bash
python3 spec/gideon-code/tools/validate_pack.py --output spec/gideon-code/evidence/authoring.json
```

It checks native syntax, requirement references and coverage, dependency cycles and wave barriers, parent/packet closure, exact vertical-packet paths, unordered ownership collisions and local document links. It records the source hashes it inspected. It never executes task build/verify commands, calls a model, creates runtime fixtures, imports the old product or marks a task done.

Native `cg spec lint` was unavailable during the original authoring pass because this checkout has no `spec/workflow.kvx`; `cg brief` also reports no initialised Codify project. This pack does not repair those unrelated project files or invent native lint evidence. The implementation preflight must resolve the task-activation interface explicitly before claiming implementation work.

An authoring report proves only those structural checks at its recorded document hashes. Product receipts, provider/platform qualification and release acceptance remain separate.

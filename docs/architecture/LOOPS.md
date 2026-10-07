# Loops

A loop is an iterative work unit with a worker, progress history, and supervisor checks.
Its strategy determines planning and deliverables; the shared runtime determines
admission, lifecycle, grants, and convergence. A running loop is not evidence that its
goal has been achieved.

The engine lives under `runtime/gideon/automation/loop/`.

## Kinds and strategies

`loop.py` defines five current kinds:

| Kind | Purpose |
|---|---|
| `general` | Generic iterative work with nudges and watchdog supervision. |
| `goal` | Goal-oriented action, verification, open-ended work, or monitoring. |
| `code` | Workspace-bound, stage-oriented software work. |
| `design` | Design steps and canvas-oriented deliverables. |
| `research` | Iterative research and synthesis. |

`kinds/` supplies strategies for classification, phases, readiness, worker selection,
and deliverable behavior. A strategy is a contribution to the existing engine contract,
not permission to bypass the engine. New kinds may also require validation, UI, and
capability support; registration alone does not qualify a complete product journey.

`manager.py`, `tick.py`, `lifecycle.py`, `store.py`, and `watchdog.py` own orchestration
and persistence. Planning briefs are specialized by kind. `gates.py`, `judge.py`, and
`supervisor.py` handle verification and supervision.

## Stages and convergence

Code work follows the SDLC metadata in `sdlc_meta.py`, including ideation, requirements,
design, decomposition, implementation, verification, and review. Different entry types
can start later in that sequence. Design, goal, research, and general strategies have
their own phase and deliverable rules.

Convergence is declared in
`runtime/gideon/automation/workflows/supervisor_policy.py`, including the closed signals
`orchestrated`, `never`, `verify_command`, and `judge_assessment`. The shared evaluator
uses the policy instead of accepting an arbitrary strategy's completion claim.
Monitoring can continue until explicitly stopped or limited by its budget; an exhausted
budget and verified goal completion are different outcomes.

Verify commands and artifact checks use real work evidence. A model assessment is still
an assessment, not a substitute for a command result, required deliverable, or independent
check. Missing verification must remain distinguishable from failure and success.

## Working directory and worktrees

`loop.py::effective_dir` resolves the directory used for worker and ground-truth checks:

1. An explicitly bound `workspace_dir`.
2. A greenfield code loop's own files directory when present.
3. The containing project's shared context directory.
4. The default session workspace.

This ordering prevents the supervisor from looking somewhere other than where the worker
was directed to write. Project state lives under the active Gideon home; an external
workspace is a separate binding.

`worktree.py` supports isolated Git worktrees for parallel tasks, using project-owned or
workspace-derived storage beneath the Gideon home. Git availability and repository
state determine whether parallel execution is possible. Unsupported or failed worktree
operations can fall back to sequential execution. Worktree creation and merge handling
do not establish that the resulting code is correct or ready for release.

## Grants and worker sessions

A loop selects a configured agent and binds its actual worker session. Current tool and
skill grants are resolved from the profile; owner-selected widening requires the exact
reviewed offer. Existing sessions refresh current grants rather than retaining a stale
broader catalogue indefinitely.

`runtime/gideon/engine/agents/loop_skills.py` permits selected intrinsic skills only from
an actual stored live loop, its registered strategy, and the host-bound worker session
with the matching agent. Arbitrary request fields and wildcard skill strings cannot
create that exception. The helper does not apply this exception to app-bound work;
outer app and task constraints remain authoritative.

Approval posture, effective risk, and unattended ceilings still apply to calls made by
a loop. A loop goal or selected agent name cannot grant standing permission by itself.
The host-owned `run_grants.py` carries the reviewed run grant surface.

## Planning and recovery

The shared planning walkthrough presents steps for review and comments before launch.
Goal-scoping and memory lookup are subject to the caller's current memory reach; planning
cannot use a guessed session or app label to gain broader context.

The watchdog handles stalls and restart adoption according to stored state and actual
execution prerequisites. Recovery is a runtime decision, not a guarantee that every
interrupted operation can be resumed safely. Surface unavailable models, workspaces,
grants, or private scope proofs rather than silently changing execution authority.

## Console surfaces

The actual loop section is
`apps/console/src/features/loops/LoopsSection.tsx`. Design work uses
`apps/console/src/features/loops/DesignCockpitPage.tsx`; the general loop cockpit is
`apps/console/src/features/loops/LoopCockpitPage.tsx`. Code work also uses
`apps/console/src/features/code/CodeCockpitPage.tsx` for workspace editing, terminal
operations, and stage context.

These surfaces project runtime state, findings, and available controls. A displayed
cycle, generated report, or disabled recovery control must be interpreted according to
its actual state and reason, not as proof of a successful end-to-end run.

See [workflows](WORKFLOWS.md), [tasks and triggers](TASKS_TRIGGERS.md),
[memory](KNOWLEDGE_MEMORY.md), and [security](SECURITY.md).

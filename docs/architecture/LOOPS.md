# Loops: the autonomous work engine

A **loop** is a long-running autonomous work unit. A worker agent iterates in
cycles toward a goal while a supervisor judges progress against ground truth.
The engine lives in `Gideon/src/gideon/loop/`, and this doc covers the five
kinds, stage progression, directory resolution, deliverable gates, and how the UI
dispatches to per-kind cockpits.

## Engine layout

`loop/` holds `manager.py` (lifecycle and orchestration), `tick.py` (the cycle
engine), `judge.py` (supervisor judgment), `gates.py` (verify-command and verdict
helpers), `lifecycle.py`, `watchdog.py` (stall detection), `worktree.py`
(parallel task isolation), `store.py` (persistence), `classify.py` (goal
classification), and the per-kind planning-brief modules `code_plan_briefs.py`,
`goal_plan_briefs.py`, `research_plan_briefs.py` and `design_plan_briefs.py`.

## The five kinds

`loop/loop.py` defines `LoopKind`:

| Kind | What it is |
|---|---|
| `general` | generic iterative goal in a chat session (nudge + watchdog) |
| `goal` | open-ended / verifiable / monitor research + action |
| `code` | SDLC stage-gated work in a workspace (mini-IDE cockpit) |
| `design` | design-system creation (live canvas, tokens, components) |
| `research` | deep iterative web research → synthesized report |

Kind behavior is pluggable. `loop/kinds/__init__.py` defines the
`LoopKindStrategy` protocol and a registry, so **a new kind is a new strategy
module plus one `register()` call, with no engine edits**. A strategy declares
its kind id, whether it needs a bound workspace, its default worker agent,
whether it provisions per-phase TaskLists at launch, its classifier, phase keys,
the deliverable document it maintains, and its readiness prerequisites. For
example, an open-ended goal keeps `REPORT.md` and a monitor keeps
`MONITOR_LOG.md`, while code has no document because the code itself is the
deliverable. Prerequisites are real gates: a brownfield code loop with no bound
workspace cannot start.

**The supervisor is not part of that plugin seam** (`PP-16` seam 3). How a loop
converges is *declared*, not coded. `workflows/supervisor_policy.py` holds the
`ConvergenceSpec` type, the closed `DONE_SIGNALS` vocabulary
(`orchestrated` | `never` | `verify_command` | `judge_assessment`) and the
`KIND_CONVERGENCE` table with one row per kind, or per `kind:goal_type` variant,
because goal-type is the axis convergence actually varies on.
`policy_for_kind(kind, kind_config)` resolves a kind to the one
`SupervisorPolicy` that carries it, and `loop/supervisor.py` is the single
kind-agnostic evaluator that reads it (`done_signal`, `has_done_check`,
`budget_stop_is_genuine`, `stagnation_enabled`). The watchdog calls only those. A
kind may not supply a convergence mechanism in Python: adding a fifth mechanism
means extending the closed vocabulary and the one evaluator, in one place, for
every kind at once.

| Kind (variant) | Declared done-signal |
|---|---|
| `code`, `design` | `orchestrated`: the per-cycle `on_new_cycle` hook owns done-ness |
| `general` | `verify_command` (optional: no command ⇒ defers to budget by design) |
| `goal:verifiable`, `research:verifiable` | `verify_command`, plus a sub-goal judge when >1 sub-goal is declared |
| `goal:open_ended`, `research:open_ended` | `judge_assessment` (ground truth: `REPORT.md` / `RESEARCH.md`) |
| `goal:monitor`, `research:monitor` | `never`: only a user Stop (or the budget, counted as a clean stop) ends it |

## Stage progression

- **Code loops** walk the canonical SDLC ladder (`loop/sdlc_meta.py`):
  `ideation → requirements → design → decomposition → implementation →
  verification → review`. Lateral entries (`bugfix`, `cr_comments`, `refactor`,
  `investigation`) start mid-ladder with a tailored shorter plan. The code
  strategy (`loop/kinds/sdlc.py`, kind id `"code"`) advances stages and
  provisions tasks each cycle.
- **Design loops** advance design steps (token system → components → …) on a
  live canvas.
- **Goal and general loops** are done when their `is_done_signal` says so, with
  no stage machinery.
- Classification (`loop/classify.py` plus the per-kind classifiers) picks the
  kind, the stop logic and the entry stage up front. Classifiers never raise:
  they return safe defaults flagged `classified=False`.

## `effective_dir`: where a loop's work actually lives

`loop/loop.py::effective_dir` is the **single resolver** every ground-truth check
uses, so the supervisor reads exactly where the worker writes. Its precedence:

1. `workspace_dir`: an explicitly bound codebase;
2. a **greenfield code loop's own `loop_dir`**, because a code loop with no bound
   workspace operates *from* its files dir (code-kind only; goal and general
   loops keep only engine files there and write deliverables to the
   project/workspace). This tier exists because its absence hard-failed the
   deliverable gate forever: the supervisor looked in the workspace root while
   the worker wrote to the loop dir, so a genuinely complete stage was "held"
   across cycles;
3. the containing project's shared context dir;
4. `workspace_root()`: the default session workspace.

## Deliverable gates and the independent judge

The supervisor does not take the worker's word for it:

- **`loop/gates.py`**: `run_verify_command` re-runs a stage's verify command
  itself (with a cwd from `effective_dir`); `judge_verdict` renders an LLM
  verdict; `verdict_is_pass` parses it strictly.
- The **SDLC gate** reads the deliverable *content*, not just its existence, and
  the **goal judge** re-runs commands or reads artifacts. Ground truth beats the
  worker's self-report.
- **`loop/watchdog.py`** detects stalls, and its own first poll re-arms loops
  left RUNNING or PLANNING by a gateway restart, so an interrupted loop resumes
  rather than zombifying (`LoopWatchdog._boot_sweep`). That sweep runs through
  `concurrency.boot_sweep`, the one boot-adoption path it shares with
  `workflows/watchdog.py` (`PP-16`). There is deliberately **no gateway boot
  hook**: a hook cannot be retried when it raises, and awaiting it delays
  startup by however long N stranded planner passes take.

## Planning walkthrough and grill

- **`planning/`** (`session.py` data model plus `runner.py` state machine) is the
  shared stepwise **gated planning walkthrough** used before launching Code and
  Goal loops: the plan is presented step by step with approve/comment gates, and
  a comment triggers a redraft of that step.
- **`grill.py`** is the memory-checked goal-scoping pipeline:
  `assess_goal → check_memory → decompose(shape) → save_decisions`. It pulls
  prior decisions and lessons so a decomposition does not re-litigate settled
  choices, and it persists new decisions as lessons. Goal loops, Projects and the
  chat skill all reuse it.

## Projects and worktrees

- **`projects.py`** is the small service layer that resolves which project a work
  unit binds to (`resolve_project_id` auto-creates one when none is chosen;
  `ensure_task_list` finds or creates the unit's TaskList under that project).
  The entity itself is the Tasks `hierarchy.Project` (`tasks/hierarchy.py`): each
  project owns `~/.gideon/projects/<id>/` with `project.json` plus `context/`,
  which is the cross-feature consolidation dir and the working area when no
  external workspace is bound.
- **`loop/worktree.py`**: parallel task execution. Workers run several tasks of
  a phase at once, each in its own git worktree under
  `projects/<project_id>/worktrees/<task_id>`, never in the user's workspace, and
  the worktrees merge back when the phase's tasks finish. A non-git workspace
  falls back to sequential execution.

## Cockpit dispatch (frontend)

`apps/console/src/pages/loops/LoopsSection.tsx` dispatches on the loop's kind:

- `kind === 'design'` → `DesignCockpitPage.tsx` (live canvas, token views, and an
  "agentic build" path that seeds a project-bound chat with the loop id, so react
  artifacts tagged `loop:<id>` render on the canvas);
- everything else → `LoopCockpitPage.tsx`, the generic loop cockpit, with the
  cycle trail, findings, sub-goal prompt bar and artifact/task/project links;
- code loops additionally get the mini-IDE at
  `apps/console/src/pages/code/CodeCockpitPage.tsx`: Monaco-based edit and save,
  PTY-backed build and test commands, and the SDLC stage trail.

## Related docs

- Tasks that loops provision: [TASKS_TRIGGERS.md](TASKS_TRIGGERS.md)
- The memory the grill consults: [KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)
- Trust and YOLO state a loop worker runs under: [SECURITY.md](SECURITY.md)

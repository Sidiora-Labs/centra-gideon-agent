# Tasks, triggers and workflows

This is the "get things done" layer: a task hierarchy shared by chat and the UI,
cron and event triggers with pluggable actions, and SOP-style workflows. Paths
are relative to `Gideon/src/gideon/`.

## Tasks

`tasks/` implements a Project → TaskList → Task hierarchy:

- **Persistence**: tasks are one JSON file each under `~/.gideon/tasks/` (ids
  `t-<hex8>`, `tasks/native.py`); task lists live under `tasks/task_lists/`;
  projects are top-level entities at `~/.gideon/projects/<id>/` (`project.json`
  plus `context/`, ids `p-<hex8>`, in `tasks/hierarchy.py`). Projects own context
  and worktrees, which is why they sit at the config root rather than under
  `tasks/`.
- **Rich task model** (`tasks/models.py`): dependencies, structured exit
  criteria, priorities, an action plan, comments.
- **Dependency reconciliation** (`tasks/reconcile.py`): on any task change, the
  changed task and its transitive dependents are re-evaluated. A task
  auto-blocks while its prerequisites are not all terminal, and auto-unblocks
  once they are. Cancelling a prerequisite counts as terminal, because a
  cancelled blocker is resolved. The graph walk tolerates cycles.
- **Honest APIs**: completing a task with unfinished exit criteria fails
  loudly at the provider layer (`tasks/native.py`: "cannot complete: unfinished
  exit criteria: …"); an invalid status on update is a 400 naming the valid set.
- **One registry, every surface** (`tasks/registry.py`): the chat `task_create`
  tool and the Tasks UI share the same provider registry, so a task created in
  conversation is the same object the board shows.

## Triggers and schedules

- **`schedule.py`**: `CronStore` (file-locked via fcntl, with mtime-based
  `_sync()` so external writes to the store are picked up) plus
  `ScheduleService`. Jobs carry a `silent` flag: a silent job suppresses
  auto-delivery (no dashboard notification, no channel post), and the agent
  decides what, if anything, to send.
- **`schedule_history.py`**: run history; **`schedule_script.py`** and
  **`schedule_trigger.py`**: script-shaped and trigger-shaped jobs.
- **`event_triggers.py`**: data-event triggers. `MemoryUpdate` fires on any
  memory write, `MemoryKeyPattern` on a write whose key matches a glob (for
  example `project.acme.*`), and `ContentMatch` on a value that matches a
  regex or substring. A `max_fires` budget auto-disables a trigger once it is
  exhausted, which is how "alert me the next time X" works.
- **`nl_to_cron.py`**: natural language to 5-field cron through a constrained
  one-shot LLM call, **validated with croniter before use**, so a hallucinated
  expression never reaches the store.

### Action providers

`action_providers/` is the pluggable "what a trigger does" registry: `bash`,
`create_task`, `invoke_agent`, `notify`, `run_prompt`, `run_script`,
`run_workflow`, `send_message` (each in its own `*_provider.py`, with `base.py`
and `registry.py`). Template variables are exported as environment variables for
the bash action.

**Dry-run is honest.** Only providers that declare `supports_dry_run`
(run-prompt, run-workflow) actually execute in observe mode. Every other action
records a `[dry run]` preview and refuses to run.

### App-manifest crons

Apps can declare crons in their manifest. `apps/app_crons.py` reconciles them on
every app lifecycle transition and always registers them `silent=True`, because
app crons are headless: the manifest flag is advisory, and a failing app cron
must not spam the owner's DM. See [APP_PLATFORM.md](APP_PLATFORM.md).

## Workflows

`workflows/` holds SOP-style reusable procedures:

- **Store and lifecycle** (`models.py`, `lifecycle.py`, `registry.py`):
  workflow names follow the skill-name rule (`^[a-z0-9][a-z0-9-]{0,62}$`).
- **Surfacing** (`surfacing.py`): when a chat message resembles a stored SOP's
  match text, the workflow is offered. The match is embedding-based behind an
  honest cosine gate (`DEFAULT_MATCH_THRESHOLD = 0.62`, tunable via
  `config.workflows.match_threshold`), and it degrades to keyword word-overlap
  when no embedding provider is bound.
- **Invocation**: the `workflow_run` and `workflow_get` tools accept an id or a
  name (names resolve through the list), so agents can call workflows the way
  users refer to them.
- MCP exposure for agents is `mcp_workflows.py`, and the schedule tools are
  `mcp_schedule.py`. See the tool-category list in
  [OVERVIEW.md](OVERVIEW.md#capability-seams).

## Related docs

- Loops provision per-phase TaskLists under a project: [LOOPS.md](LOOPS.md)
- Memory writes that event triggers observe:
  [KNOWLEDGE_MEMORY.md](KNOWLEDGE_MEMORY.md)
- Where trigger results get delivered: [INBOX_CHANNELS.md](INBOX_CHANNELS.md)

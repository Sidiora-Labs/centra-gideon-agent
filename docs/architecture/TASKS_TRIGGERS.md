# Tasks, triggers and workflows

Tasks describe work and its completion criteria. Triggers decide when an accepted action
may run. Workflows describe a graph of execution. Their stores, identities and authority
are distinct even when one creates or refers to another. Paths below are relative to
the repository root.

## Tasks and projects

`runtime/gideon/engine/tasks` implements the Project → TaskList → Task hierarchy.
Tasks are stored under the home's `tasks` directory; task lists are under
`tasks/task_lists`. Projects live under the home's `projects/<id>` directory and
own their context and worktree locations. `hierarchy.py` and `native.py` are the
store/implementation entrypoints; `registry.py` supplies the provider seam used
by chat tools and console handlers.

Tasks include dependencies, status, priority, structured exit criteria and comments.
`reconcile.py` recalculates dependent work when prerequisites change. Completion
validation happens at the provider, not merely in a disabled UI control. Revisioned
writes retain the original read's revision; a later timestamp must distinguish an
immediate second mutation from its predecessor.

Workflow-produced tasks are projections of actual run state with engine-owned fields.
An owner-authored task and a managed run projection do not have the same mutation
contract. Deleting or completing a card must use the provider's applicable operation.

## Scheduled and event work

`runtime/gideon/automation/schedule.py` defines schedule records and shared clock/display
policy. The active trigger substrate lives in `automation/triggers`, including store,
service, dispatch, delivery and action-provider consumers. Schedule view and migration
helpers are distinct from the canonical trigger's accepted execution state.

Clock, lifecycle and data-event triggers can invoke registered actions. Natural-language
cadence conversion (`automation/nl_to_cron.py`) validates the resulting cron expression
before it is admitted. An enabled stored record is not by itself permission to execute:
current protected acceptance, action revision, provider/template pins and applicable
grants are consulted on dispatch.

`runtime/gideon/integrations/action_providers` defines the action contracts. Actual
providers decide whether they support dry run. A preview must not be described as an
executed action, and unsupported dry-run behavior must not silently perform the effect.
Actions can report tracked completion so running history follows actual work, rather
than treating a background launch acknowledgement as terminal success.

## App-declared jobs

`runtime/gideon/extensions/apps/app_crons.py` reconciles manifest jobs on app lifecycle
changes. App jobs are silent/headless: silence controls automatic notifications and
result delivery, not authority to invoke tools.

The canonical stored job must match the installed enabled app and its current declared
agent tier. Protected accepted action identity remains the original trigger origin;
app execution is an effective narrowing of that origin. Descendants cannot acquire
owner authority by dropping the trigger actor or supplying an app-shaped string.

App jobs cannot choose their own `approval_mode` or `capability` to widen the manifest
tier. Unsupported posture is refused explicitly. Supported owner edits to cadence,
message and display name do not grant additional capabilities. Disabling the app or
revoking the relevant acceptance refuses subsequent work.

## Workflow runs

`runtime/gideon/automation/workflows` implements declarative graph runs. `service.py`
accepts starts, `store.py` persists definitions and runs, `controller.py` owns scheduling,
and `engine.py` dispatches actual nodes/actions. Template surfacing can offer a procedure;
it does not automatically grant execution.

Accepted version bounds and verified origin travel with child runs. App work ceilings,
action deny rules, egress and tool approvals remain separate checks. Temporary runs
require native private-work issuance, a live receipt and actual lifecycle cleanup.
Mutable run metadata or a privacy label cannot recreate a missing receipt.

See [Workflows](WORKFLOWS.md), [Loops](LOOPS.md), [App platform](APP_PLATFORM.md),
[Knowledge and memory](KNOWLEDGE_MEMORY.md) and [Security architecture](SECURITY.md).

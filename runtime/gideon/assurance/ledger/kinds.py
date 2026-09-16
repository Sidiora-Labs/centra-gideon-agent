"""The ledger's event vocabulary — one registry, shared by every producer.

These names were born inside the workflow engine, but nothing about them is workflow-shaped: a
`step_started` is "a unit of work began", a `judge_verdict` is "something assessed something", a
`breaker_trip` is "a loop was cut off". The vocabulary lives here rather than in
`workflows/journal.py` so a second producer can speak it without importing the engine — which is
the only alternative that keeps ONE vocabulary. A loop that emitted its own `cycle_finished`
alongside `step_completed` would not be a second producer of the ledger; it would be a fifth
dialect for a reader to reconcile, which is the failure the extraction exists to prevent.

Adding a kind is therefore additive here and nowhere else: the constant, its comment saying what
question the kind answers, and (if a refiner must read it) an entry in :data:`LEDGER_KINDS`.
"""

from __future__ import annotations

STEP_STARTED = "step_started"
STEP_COMPLETED = "step_completed"
STEP_FAILED = "step_failed"
STEP_SKIPPED = "step_skipped"
STEP_CACHED = "step_cached"
STEP_ATTEMPT = "step_attempt"
STEP_ESCALATED = "step_escalated"
GATE_REJECTED = "gate_rejected"
GATE_CRITERION = "gate_criterion"
GATE_RESOLVED = "gate_resolved"
GATE_REVISED = "gate_revised"
EFFECT = "effect"
STEP_SCOPE = "step_scope_violation"
ITERATION = "iteration"
USER_EDITED_MID_FLIGHT = "user_edited_mid_flight"
MUTATION_REJECTED = "mutation_rejected"
INPUTS_STALE = "inputs_stale"
CONSULTED = "consulted"
CHILD_RUN_ATTACH = "child_run_attach"
RUN_ABANDONED = "run_abandoned"
CRYSTALLIZED = "crystallized"
HANDOFF = "handoff"
CARRYOVER = "carryover"
DECISION = "decision"
RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"
BREAKER_TRIP = "breaker_trip"
STEERING = "steering"
ITEMS_COLLECTED = "items_collected"
JUDGE_VERDICT = "judge_verdict"
JUDGE_DIVERGENCE = "judge_divergence"
WATCHER_REAPED = "watcher_reaped"
SEEN_SET = "seen_set"
BUFFER_SEAL = "buffer_seal"
DELAY_CLAMPED = "delay_clamped"

CLOCK_READ = "clock_read"

TILE_REFRESHED = "tile_refreshed"

PENDING_OUTCOME = "pending_outcome"
OUTCOME_RESOLVED = "outcome_resolved"

TASK_MATERIALIZED = "task_materialized"
CONFIRMATION_PENDING = "confirmation_pending"
CONFIRMATION_RESOLVED = "confirmation_resolved"
TASK_VERIFIED = "task_verified"
CASCADE_BLOCKED = "cascade_blocked"

WORKSPACE_PROVISIONED = "workspace_provisioned"
WORKSPACE_TEARDOWN = "workspace_teardown"

SKIPPED_TRIAGE = "skipped_triage"
PROPOSAL_REFUSED = "proposal_refused"

AUTO_EXECUTED = "auto_executed"
SKIPPED_BUDGET = "skipped_budget"

REVIEW_FINDING = "review_finding"

TRIAGE_REPLY = "triage_reply"

LEDGER_KINDS = frozenset(
    {
        SKIPPED_TRIAGE,
        PROPOSAL_REFUSED,
        AUTO_EXECUTED,
        SKIPPED_BUDGET,
        TRIAGE_REPLY,
        STEP_COMPLETED,
        STEP_FAILED,
        STEP_SKIPPED,
        STEP_CACHED,
        STEP_ATTEMPT,
        STEP_ESCALATED,
        GATE_REJECTED,
        GATE_CRITERION,
        GATE_RESOLVED,
        GATE_REVISED,
        EFFECT,
        STEP_SCOPE,
        MUTATION_REJECTED,
        INPUTS_STALE,
        ITERATION,
        USER_EDITED_MID_FLIGHT,
        CONSULTED,
        CHILD_RUN_ATTACH,
        RUN_ABANDONED,
        CRYSTALLIZED,
        HANDOFF,
        CARRYOVER,
        TASK_MATERIALIZED,
        CONFIRMATION_PENDING,
        CONFIRMATION_RESOLVED,
        TASK_VERIFIED,
        CASCADE_BLOCKED,
        DECISION,
        BREAKER_TRIP,
        STEERING,
        ITEMS_COLLECTED,
        JUDGE_VERDICT,
        JUDGE_DIVERGENCE,
        REVIEW_FINDING,
        WATCHER_REAPED,
        SEEN_SET,
        BUFFER_SEAL,
        DELAY_CLAMPED,
        CLOCK_READ,
        TILE_REFRESHED,
        PENDING_OUTCOME,
        OUTCOME_RESOLVED,
        WORKSPACE_PROVISIONED,
        WORKSPACE_TEARDOWN,
    }
)

from gideon.hypermid.diagnostics import (
    ContextDiagnostics,
    DiagnosticFailure,
    DiagnosticOutcome,
    DiagnosticStore,
    ModelBudgetDiagnostics,
    RegionDiagnostics,
    UsageAccounting,
)
from gideon.hypermid.models import Cursor, Scope, Trace


def _diagnostics() -> ContextDiagnostics:
    digest = "0" * 64
    return ContextDiagnostics(
        scope=Scope("owner-1", "project-1"),
        session_id="session-1",
        cursor=Cursor(1, 7),
        generation=2,
        mode="primary",
        provider_profile_digest=digest,
        journal_items=7,
        journal_bytes=4096,
        model_budget=ModelBudgetDiagnostics(
            context_window_tokens=16_000,
            reserved_output_tokens=2_000,
            max_input_tokens=14_000,
            max_items=100,
            max_images=8,
            baseline_tokens=4_000,
            delta_tokens=1_000,
            tail_tokens=2_000,
            confidence="measured",
        ),
        pressure_band="normal",
        baseline=RegionDiagnostics("baseline", digest, ("item-1",), (), 4_000),
        delta=RegionDiagnostics("delta", digest, ("item-2",), (), 1_000),
        tail=RegionDiagnostics("tail", digest, ("item-3",), (), 2_000),
        pending_reductions=1,
        summary_jobs=1,
        last_known_good_eligible=True,
        last_reason_code="projection_committed",
        foreground_usage=UsageAccounting("reported", 100, 20, 30, 10),
        summary_usage=UsageAccounting.reported_zero(),
        subagent_usage=UsageAccounting.no_call(),
        generated_at="2026-10-02T13:00:00Z",
        protected_item_ids=("item-3",),
        selected_tiers={"summary-1": 2},
        recent_outcomes=(
            DiagnosticOutcome(
                "2026-10-02T13:00:00Z",
                "projection_committed",
                Trace("trace-1", "request-1"),
                "projection committed",
            ),
        ),
    )


def test_diagnostics_keep_last_good_and_report_parked_failure_without_secret_content() -> (
    None
):
    store = DiagnosticStore(retention=2)
    good = _diagnostics()
    store.record_good(good, observed_at_ms=10)

    failed = store.record_failure(
        DiagnosticFailure(
            "engine_timeout",
            "Authorization: Bearer reusable-secret",
            True,
            20,
        ),
        observed_at_ms=20,
        parked=True,
    )

    assert failed.disposition == "parked"
    assert failed.value is None
    assert failed.last_good is good
    assert failed.current_error is not None
    assert failed.current_error.message == "diagnostic detail redacted"
    assert UsageAccounting.missing() != UsageAccounting.reported_zero()
    assert len(store.retained()) == 2
    assert "reusable-secret" not in str(failed.to_wire())

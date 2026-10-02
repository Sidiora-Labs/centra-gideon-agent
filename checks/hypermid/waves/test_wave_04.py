"""Wave 4 budgeted background knowledge maintenance acceptance."""

from pathlib import Path

from checks.hypermid.verify_wave_04 import verify


def test_budgeted_background_knowledge_maintenance(tmp_path: Path) -> None:
    result = verify(tmp_path)
    assert result["knowledge_cycle_completed"]
    assert result["note_conditions_inspected"]
    assert result["code_verification_inspected"]
    assert result["git_recall_consumed"]
    assert result["edited_memory_invalidated"]
    assert result["cancelled_write_refused"]
    assert result["stale_source_refused"]
    assert result["stale_config_refused"]
    assert result["foreign_owner_refused"]
    assert result["atomic_summary"]
    assert result["wrong_fence_rejected"]
    assert result["completed_range_not_reprocessed"]
    assert result["privacy_no_call"]
    assert result["quiesce_no_call"]
    assert result["budget_no_call"]
    assert result["usage_rows"] == 1
    assert result["measured_input_tokens"] > 0
    assert result["measured_output_tokens"] > 0
    assert result["measured_cache_read_tokens"] >= 0
    assert result["measured_cache_creation_tokens"] >= 0
    assert result["cost_status"] == "unpriced"
    assert result["price_source"] == "unknown"
    assert result["financial_reservation_unresolved"]
    assert "actual_cost_nanodollars" not in result
    assert result["provider_usage_reconciled"]
    assert result["redaction_passed"]
    assert result["approved_credentials"]
    assert result["note_attributed"]
    assert result["git_guarded_and_revalidated"]

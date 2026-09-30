"""One effective model price flows through guarded spend and usage projections."""

from __future__ import annotations

import json

from gideon.engine.routing import rates, stats, telemetry, usage
from gideon.integrations.llm.base import LLMEvent
from gideon.operations import pricing, usage_ledger
from gideon.security.guardrails.budgets import SpendMeter
from gideon.security.guardrails.audit import AttemptRecord, record_attempt


def test_resolved_rate_matches_budget_audit_ledger_and_usage(tmp_path, monkeypatch):
    from gideon.core.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    rates._overlay_cache = None
    rates.save_overlay(
        {
            "acme:qwen3": {
                "in_per_mtok": 4.0,
                "out_per_mtok": 12.0,
                "cache_read_per_mtok": 2.0,
                "cache_write_per_mtok": 20.0,
            }
        },
        home=tmp_path,
    )
    event = LLMEvent(
        kind="complete",
        input_tokens=1_000,
        output_tokens=200,
        cache_read_tokens=100,
        cache_creation_tokens=50,
    )
    resolved = rates.resolve_effective_price(
        "acme",
        "qwen3",
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        cache_read_tokens=event.cache_read_tokens,
        cache_creation_tokens=event.cache_creation_tokens,
    )
    expected = 0.0076
    assert resolved.cost_usd == expected and resolved.priced
    assert resolved.source == "overlay" and resolved.estimated
    assert pricing.estimate_cost(
        "qwen3",
        event.input_tokens,
        event.output_tokens,
        event.cache_read_tokens,
        event.cache_creation_tokens,
        provider="acme",
    ) == expected

    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(event.input_tokens + event.output_tokens, resolved.cost_usd)
    assert meter.day_totals().dollars == expected
    record_attempt(
        AttemptRecord(
            audit_id="price-audit",
            ts=1.0,
            use_case="pricing-test",
            provider="acme",
            model="qwen3",
            attempt=1,
            latency_ms=10.0,
            tokens_in=event.input_tokens,
            tokens_out=event.output_tokens,
            dollars_est=resolved.cost_usd,
            estimated=resolved.estimated,
            passed=True,
            query_class="pricing-test",
            extra={"priced": resolved.priced, "price_source": resolved.source},
        )
    )
    audit_row = json.loads(
        (tmp_path / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert audit_row["dollars_est"] == expected
    assert audit_row["priced"] is True and audit_row["price_source"] == "overlay"

    usage_ledger.record_from_event(
        event,
        source="chat",
        provider="acme",
        model="qwen3",
        estimate_if_missing=True,
    )
    ledger_row = usage_ledger._iter_rows()[0]
    assert ledger_row["cost_usd"] == expected and ledger_row["priced"] is True
    folded = usage.empty_fold()
    assert usage.fold_turn_row(folded, ledger_row, look=usage._rate_lookup(tmp_path))
    view = usage.query(folded, window="month", group="model")
    model_row = next(row for row in view["rows"] if row["key"] == "acme:qwen3")
    assert model_row["dollars_est"] == expected and model_row["priced"] is True

    stats_fold = stats.fold_record(stats._empty(), audit_row, now="now")
    projected = telemetry.telemetry_rows(
        stats_fold, [audit_row], "pricing-test", "pricing-test"
    )
    assert projected[0]["avg_cost_usd"] == expected
    assert projected[0]["priced"] is True and projected[0]["price_source"] == "overlay"

    provider_cost = rates.resolve_effective_price(
        "acme", "qwen3", reported_cost_usd=0.25
    )
    assert provider_cost.cost_usd == 0.25 and provider_cost.source == "provider_reported"
    reported_event = LLMEvent(
        kind="complete",
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        cost_usd=0.25,
        tool_meta={"usage_reported": True},
    )
    reported_row = usage_ledger.EventAccounting(
        reported_event, "qwen3", True
    ).record("chat", "reported-session", "", "acme")
    assert reported_row.cost_usd == 0.25
    assert reported_row.priced and not reported_row.estimated
    assert reported_row.price_source == "provider_reported"
    unknown = rates.resolve_effective_price("remote-provider", "unlisted-model")
    assert unknown.cost_usd is None and not unknown.priced
    assert rates.cost_for("remote-provider", "unlisted-model") is None

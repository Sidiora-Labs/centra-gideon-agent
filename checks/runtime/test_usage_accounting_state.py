import json
from datetime import datetime, timezone

import pytest

from gideon.engine.routing import rates, usage
from gideon.operations import usage_ledger
from gideon.operations.usage_ledger import TurnUsage


def turn(day, provider="cloud", model="priced", source="chat", cost=1, priced=True):
    return TurnUsage(
        ts=day + "T12:00:00+00:00",
        session_key="dashboard:usage",
        source=source,
        agent="",
        provider=provider,
        model=model,
        input_tokens=10,
        output_tokens=2,
        cost_usd=cost,
        priced=priced,
    )


def test_actual_ledger_discloses_estimates_unknown_prices_and_separate_attempts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    rates.save_overlay(
        {"cloud:priced": {"in_per_mtok": 1, "out_per_mtok": 2}}, home=tmp_path
    )
    day = "2026-09-15"
    records = [
        turn(day, cost=2),
        turn(day, source="weather", cost=1),
        turn(day, provider="unlisted", model="unknown", cost=0, priced=False),
        turn(day, provider="ollama", model="local", source="loop", cost=0),
    ]
    for record in records:
        usage_ledger.record_turn(record)
    ledger = usage_ledger._path()
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    rows[0]["estimated"] = False
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    stamp = datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp()
    (tmp_path / "model_calls.jsonl").write_text(
        json.dumps({"ts": stamp, "use_case": "loops", "dollars_est": 99}) + "\n"
    )
    fold = usage.refresh(tmp_path)
    view = usage.query(fold, today=day, group="purpose")
    assert view["total"]["calls"] == 4
    assert view["total"]["dollars_est"] == 3
    assert view["total"]["estimated_share"] == 0.3333
    assert view["total"]["unpriced_calls"] == 1
    assert view["total"]["local_calls"] == 1
    assert not view["total"]["priced"]
    assert view["uncounted"]["total_dollars_est"] == 99
    assert view["app_sources"] == {"weather": 1}
    recap = usage.usage_recap("2026-09", fold=fold)
    assert "1 turn ran on a model with no price row" in recap
    assert "1 unattended model call" in recap


def test_refresh_preserves_trimmed_days_but_rebuild_uses_only_retained_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    for record in [turn("2026-09-14"), turn("2026-09-14"), turn("2026-09-15")]:
        usage_ledger.record_turn(record)
    first = usage.refresh(tmp_path)
    ledger = usage_ledger._path()
    ledger.write_text(ledger.read_text().splitlines()[-1] + "\n")
    retained = usage.refresh(tmp_path)
    assert retained["days"] == first["days"]
    assert retained["sources"]["usage_ledger"] == 1
    rebuilt = usage.rebuild(tmp_path)
    assert set(rebuilt["days"]) == {"2026-09-15"}
    assert (
        usage.query(rebuilt, window="week", today="2026-09-15")["total"]["calls"] == 1
    )


def test_rate_presence_is_scoped_to_one_fold_and_bad_dates_remain_visible(tmp_path):
    probe = usage._rate_lookup(tmp_path)
    assert probe("acme", "unknown") == (True, False)
    rates.save_overlay(
        {"acme:unknown": {"in_per_mtok": 0, "out_per_mtok": 0}}, home=tmp_path
    )
    assert probe("acme", "unknown") == (True, False)
    assert usage._rate_lookup(tmp_path)("acme", "unknown") == (False, False)
    fold = usage.empty_fold()
    assert not usage.fold_turn_row(fold, {"provider": "acme"}, look=probe)
    assert fold["unmapped"] == {"row:no_date": 1}
    assert usage.window_dates("week", today="2024-03-01")[-2:] == [
        "2024-02-29",
        "2024-03-01",
    ]
    assert usage._share({"dollars_est": float("nan")}) == 1.0

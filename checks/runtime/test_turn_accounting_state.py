from gideon.integrations.llm.base import EVENT_COMPLETE, LLMEvent
from gideon.operations import pricing
from gideon.operations import usage_ledger as ledger


def record(identifier):
    return ledger.TurnUsage(
        ts="2026-09-16T12:00:00+00:00",
        session_key=identifier,
        source="loop",
        agent="",
        provider="test",
        model="unpriced",
        input_tokens=10,
        output_tokens=2,
        cost_usd=0.25,
        priced=False,
    )


def test_real_event_projection_preserves_reported_and_estimated_cost(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    event = LLMEvent(
        kind=EVENT_COMPLETE,
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_creation_tokens=0,
    )
    ledger.record_from_event(
        event, source="cli", session_key="cli:one", model="claude-sonnet-4.5"
    )
    ledger.record_from_event(
        LLMEvent(kind=EVENT_COMPLETE, cost_usd=0.75), source="chat", model="unpriced"
    )
    ledger.record_from_event(
        event, source="chat", model="unpriced", estimate_if_missing=False
    )
    rows = ledger._iter_rows()
    assert rows[0]["cost_usd"] == 3.3 and rows[0]["priced"]
    assert rows[0]["cache_read_tokens"] == 1_000_000
    assert rows[1]["cost_usd"] == 0.75 and rows[1]["priced"]
    assert rows[2]["cost_usd"] == 0 and not rows[2]["priced"]


def test_actual_journal_trim_keeps_newest_rows_and_survives_corrupt_tail(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(ledger, "_CAP", 2)
    for index in range(5):
        ledger.record_turn(record(f"loop-{index}"))
    assert [row["session_key"] for row in ledger._iter_rows()] == ["loop-3", "loop-4"]
    with ledger._path().open("a") as stream:
        stream.write("{partial\n17\n")
    assert ledger.totals()["turns"] == 2
    assert not list(ledger._path().parent.glob("*.tmp*"))


def test_session_prefix_matches_only_separator_bounded_descendants(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    for key in ("loop-abc", "loop-abc-task", "loop-abc9", "loop-ab"):
        ledger.record_turn(record(key))
    assert ledger.totals(session_prefix="loop-abc")["turns"] == 2
    assert (
        ledger.totals(session_prefix="loop-abc", session_key="loop-abc-task")["turns"]
        == 1
    )
    assert (
        ledger.totals(session_prefix="loop-abc", until="2026-09-16T12:00:00+00:00")[
            "turns"
        ]
        == 0
    )
    assert (
        ledger.rollup(session_prefix="loop-abc", group_by="day")[0]["day"]
        == "2026-09-16"
    )


def test_real_storage_failure_is_suppressed_and_price_lookup_keeps_raw_priority(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "usage").write_text("occupied")
    ledger.record_turn(record("blocked"))
    assert (tmp_path / "usage").read_text() == "occupied"
    raw = {"in": 7.0, "out": 8.0}
    family = {"in": 3.0, "out": 4.0}
    table = pricing.PriceTable({"claude-opus-4-8": raw, "claude-opus-4.8": family})
    assert table.lookup("claude-opus-4-8-20990101") is raw
    assert table.lookup("us.anthropic.claude-opus-4-8-20990101-v1:0") is family
    assert pricing.TokenCharge(1_000_000, 0, 1_000_000, 1_000_000).at(family) == 6.0

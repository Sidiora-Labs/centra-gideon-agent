"""TokenJuice savings ledger (Context Economy §1.3) — aggregated, bounded, best-effort."""

from __future__ import annotations

import pytest

from gideon.integrations.tool_providers import savings


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import gideon.core.config.loader as cfg
    import gideon.integrations.tool_providers.savings as sv

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sv, "config_dir", lambda: tmp_path)
    return tmp_path


def test_empty_summary_is_safe():
    s = savings.summary()
    assert s["saved_chars"] == 0 and s["top_compressor"] is None and s["rows"] == []


def test_records_and_aggregates():
    savings.record_saving(
        month="2026-07", model="unknown", compressor="log", chars_in=1000, chars_out=100
    )
    savings.record_saving(
        month="2026-07", model="unknown", compressor="log", chars_in=2000, chars_out=200
    )
    s = savings.summary()
    assert s["projection_count"] == 2
    assert s["by_compressor"]["log"] == 2700
    assert s["saved_chars"] == 2700
    assert s["top_compressor"] == "log"
    assert s["saved_tokens_estimated"] == 2700 // 4
    assert s["estimated"] is True


def test_rows_are_bounded_by_key_not_call_count():
    for _ in range(100):
        savings.record_saving(
            month="2026-07",
            model="unknown",
            compressor="json",
            chars_in=500,
            chars_out=50,
        )
    s = savings.summary()
    assert len(s["rows"]) == 1
    assert s["rows"][0]["count"] == 100


def test_non_saving_projection_not_recorded():
    savings.record_saving(
        month="2026-07", model="x", compressor="log", chars_in=100, chars_out=100
    )
    savings.record_saving(
        month="2026-07", model="x", compressor="log", chars_in=100, chars_out=200
    )
    assert savings.summary()["rows"] == []


def test_top_compressor_is_the_biggest_saver():
    savings.record_saving(
        month="2026-07", model="u", compressor="log", chars_in=1000, chars_out=900
    )
    savings.record_saving(
        month="2026-07", model="u", compressor="diff", chars_in=5000, chars_out=100
    )
    assert savings.summary()["top_compressor"] == "diff"


def test_corrupt_file_reads_as_empty(tmp_path):
    (tmp_path / "tokenjuice_savings.json").write_text("{not json", encoding="utf-8")
    assert savings.summary()["saved_chars"] == 0
    savings.record_saving(
        month="2026-08", model="u", compressor="csv", chars_in=400, chars_out=40
    )
    assert savings.summary()["saved_chars"] == 360

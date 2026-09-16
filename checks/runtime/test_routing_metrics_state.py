import json
import os

import pytest

from gideon.engine.routing import rates, stats, telemetry


def attempt(index, **overrides):
    return {
        "provider": "local",
        "model": "runner:7b",
        "use_case": "chat",
        "query_class": "short_chat",
        "passed": index != 2,
        "latency_ms": 100 * index,
        "dollars_est": index / 1000,
        "ts": str(index),
        **overrides,
    }


def test_live_fold_and_rebuild_produce_identical_estimates_and_percentiles(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    rows = [attempt(index) for index in range(1, 5)]
    for row in rows:
        stats.record_routing_stats(row, home=tmp_path, now=row["ts"])
    live = stats.load_stats(tmp_path)
    audit = tmp_path / "model_calls.jsonl"
    audit.write_text("\n".join(json.dumps(row) for row in rows) + "\n[]\n{broken\n{}\n")
    assert stats.rebuild(tmp_path, audit) == 4
    assert stats.load_stats(tmp_path) == live
    view = telemetry.telemetry_rows(live, rows, "chat", "short_chat")
    assert view[0]["n"] == 4
    assert view[0]["p50_ms"] == 200
    assert view[0]["p95_ms"] == 400
    assert view[0]["on_frontier"]
    assert (
        live["use_cases"]["chat"]["short_chat"]["local:runner:7b"]["success_rate"]
        == 0.872
    )


def test_rate_cache_detects_atomic_same_size_and_timestamp_replacement(tmp_path):
    path = rates.save_overlay(
        {"local:runner": {"in_per_mtok": 1, "out_per_mtok": 2}}, home=tmp_path
    )
    assert rates.rate_for("local", "runner", home=tmp_path).in_per_mtok == 1
    before = path.stat()
    replacement = tmp_path / "replacement"
    replacement.write_text(
        path.read_text().replace('"in_per_mtok": 1', '"in_per_mtok": 9')
    )
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement.replace(path)
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_ino != before.st_ino
    assert rates.cost_for("local", "runner", home=tmp_path, input_tokens=1_000_000) == 9
    path.unlink()
    assert rates.rate_for("ollama", "runner", home=tmp_path).source == "local"
    assert rates.rate_for("unlisted-vendor", "unlisted-model", home=tmp_path) is None


def test_failed_fold_write_does_not_publish_a_proposal(tmp_path, caplog):
    blocked_home = tmp_path / "file"
    blocked_home.write_text("owned")
    stats.record_routing_stats(attempt(1), home=blocked_home)
    assert blocked_home.read_text() == "owned"
    assert "routing stats fold failed" in caplog.text
    assert sorted(path.name for path in tmp_path.iterdir()) == ["file"]


def test_rate_pattern_ties_and_invalid_versions_retain_contract(tmp_path):
    first, second = {"in_per_mtok": 1}, {"in_per_mtok": 2}
    assert rates._match_key({"a?c": first, "ab?": second}, ["abc"]) is first
    assert rates._match_key({"a*": None, "bare": first}, ["abc", "bare"]) is None
    path = rates.save_overlay({"x": first}, home=tmp_path)
    payload = json.loads(path.read_text())
    payload["version"] = "invalid"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        rates.load_overlay(tmp_path)

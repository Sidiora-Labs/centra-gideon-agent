"""Scheduled research reports — definition round-trip and hardened dueness (WF2KNO-12).

Each of the five rules named in ``knowledge/research_reports.py``'s docstring gets a
test here. They are regression tests for scheduling bugs that are invisible in a demo
and obvious in production: firing at 1970, firing fifty times after a laptop nap,
skipping a window after a transient failure, and skipping an item forever because the
watermark was stamped at completion.

Tests that call ``record_run`` anchor their simulated clock on the real one, because
``record_run`` stamps ``time.time()`` by design (rule 3) — a synthetic "now" far from
the wall clock would make the recorded run look either ancient or far in the future.
"""

from __future__ import annotations

import json
import time

import pytest

from gideon.automation.schedule import ScheduleDefinition
from gideon.cognition.knowledge.research_reports import (
    ALLOW_CITING_CONTEXT,
    CITE_SOURCE_ONLY,
    FINDING_KIND,
    MAX_ITERATION_CAP,
    ReportDefinition,
    Scope,
    delete_report,
    from_dict,
    get_report,
    is_due,
    load_reports,
    record_run,
    save_report,
    to_dict,
)

HOUR = 3600.0

FIXED_NOW = 1_798_806_600.0


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """`GIDEON_HOME` *and* the module-bound `config_dir` — the env var alone
    would still be missed by anything that bound the function at import time, and the
    real `~/.gideon` must never be touched by this suite."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setattr(
        "gideon.cognition.knowledge.research_reports.config_dir", lambda: home
    )
    return home


def _store_file(home):
    return home / "research_reports.json"


def _defn(**over) -> ReportDefinition:
    kw: dict = {
        "id": "",
        "name": "Weekly deps",
        "prompt": "What changed in my dependency sources?",
        "schedule": ScheduleDefinition(kind="every", every_secs=int(HOUR)),
    }
    kw.update(over)
    return ReportDefinition(**kw)


def _saved_with_last_run(
    report_id: str, *, created_ts: float, last_run_ts: float, **over
):
    """Persist a report that has already run once — the state every catch-up bug hides in."""
    defn = save_report(_defn(id=report_id, created_ts=created_ts, **over))
    defn.last_run_ts = last_run_ts
    return save_report(defn)


def test_absent_store_loads_empty():
    assert load_reports() == []


def test_corrupt_store_loads_empty_and_never_raises(_isolated_home):
    _store_file(_isolated_home).write_text("{not json at all", encoding="utf-8")
    assert load_reports() == []
    _store_file(_isolated_home).write_text('{"id": "x"}', encoding="utf-8")
    assert load_reports() == []


def test_save_assigns_id_and_created_ts():
    saved = save_report(_defn())
    assert saved.id
    assert saved.created_ts > 0
    assert get_report(saved.id) is not None
    assert [d.id for d in load_reports()] == [saved.id]


def test_save_replaces_by_id_and_delete_reports_whether_it_removed_anything():
    saved = save_report(_defn())
    saved.name = "renamed"
    save_report(saved)
    assert [d.name for d in load_reports()] == ["renamed"]
    assert delete_report(saved.id) is True
    assert delete_report(saved.id) is False
    assert load_reports() == []


def test_save_rejects_unknown_citation_policy():
    with pytest.raises(ValueError, match="invalid citation_policy"):
        save_report(_defn(citation_policy="cite-whatever"))


def test_save_clamps_iteration_cap():
    assert save_report(_defn(iteration_cap=9999)).iteration_cap == MAX_ITERATION_CAP
    assert save_report(_defn(id="z", iteration_cap=0)).iteration_cap == 1


def test_round_trip_is_json_safe_with_context_none():
    defn = save_report(_defn(citation_policy=CITE_SOURCE_ONLY))
    raw = to_dict(defn)
    json.dumps(raw)
    assert raw["context"] is None
    assert from_dict(raw) == defn


def test_round_trip_with_populated_context_scope():
    defn = save_report(
        _defn(
            source=Scope(tags=("deps", "security"), window_secs=0),
            context=Scope(tags=("notes",), window_secs=7 * 86400),
            citation_policy=ALLOW_CITING_CONTEXT,
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 7 * * 1"),
            tz="America/Los_Angeles",
        )
    )
    back = from_dict(to_dict(defn))
    assert back == defn
    assert back.source.tags == ("deps", "security")
    assert back.context is not None and back.context.window_secs == 7 * 86400
    assert back.schedule.cron_expr == "0 7 * * 1"


def test_from_dict_is_tolerant_of_unknown_keys_and_bad_types():
    back = from_dict(
        {
            "id": "r1",
            "name": 17,
            "prompt": "p",
            "schedule": {"kind": "every", "every_secs": "60"},
            "source": "nonsense",
            "context": {"tags": ["a", 5, ""], "window_secs": -9},
            "citation_policy": "bogus",
            "iteration_cap": 500,
            "enabled": "yes",
            "last_run_ts": "never",
            "totally_unknown_key": True,
        }
    )
    assert back.name == ""
    assert back.schedule.every_secs is None
    assert back.source == Scope()
    assert back.context is not None and back.context.tags == ("a",)
    assert back.context.window_secs == 0
    assert back.citation_policy == CITE_SOURCE_ONLY
    assert back.iteration_cap == MAX_ITERATION_CAP
    assert back.enabled is True
    assert back.last_run_ts is None


def test_finding_kind_is_the_shared_constant():
    assert FINDING_KIND == "research-finding"


def test_malformed_cron_is_not_due_and_never_raises():
    for expr in ("99 * * * *", "not a cron", "* * * *", "0 0 * * MOO", ""):
        defn = _defn(
            id="bad",
            schedule=ScheduleDefinition(kind="cron", cron_expr=expr),
            created_ts=FIXED_NOW - 10 * HOUR,
        )
        due, reason = is_due(defn, now=FIXED_NOW)
        assert due is False
        assert "invalid cron expression" in reason
        assert repr(expr) in reason


def test_unsupported_schedule_kind_fails_closed():
    defn = _defn(id="k", schedule=ScheduleDefinition(kind="weekly-ish"), created_ts=1.0)
    due, reason = is_due(defn, now=FIXED_NOW)
    assert due is False
    assert "weekly-ish" in reason


def test_nonsense_every_secs_fails_closed():
    defn = _defn(
        id="e",
        schedule=ScheduleDefinition(kind="every", every_secs=0),
        created_ts=FIXED_NOW - 10 * HOUR,
    )
    due, reason = is_due(defn, now=FIXED_NOW)
    assert due is False
    assert "every_secs" in reason


def test_disabled_report_is_never_due():
    defn = _defn(id="off", enabled=False, created_ts=FIXED_NOW - 10 * HOUR)
    assert is_due(defn, now=FIXED_NOW) == (False, "disabled")


def test_new_report_anchors_first_fire_on_creation_not_the_epoch():
    fresh = _defn(id="fresh", created_ts=FIXED_NOW - 60.0, last_run_ts=None)
    due, reason = is_due(fresh, now=FIXED_NOW)
    assert due is False, reason

    aged = _defn(id="aged", created_ts=FIXED_NOW - 2 * HOUR, last_run_ts=None)
    assert is_due(aged, now=FIXED_NOW)[0] is True


def test_report_with_no_creation_time_fails_closed_rather_than_anchoring_on_1970():
    orphan = _defn(id="orphan", created_ts=0.0, last_run_ts=None)
    due, reason = is_due(orphan, now=FIXED_NOW)
    assert due is False
    assert "epoch" in reason


def test_cron_never_run_also_anchors_on_creation():
    hourly = ScheduleDefinition(kind="cron", cron_expr="0 * * * *")
    fresh = _defn(id="cfresh", schedule=hourly, tz="UTC", created_ts=FIXED_NOW - 60.0)
    assert is_due(fresh, now=FIXED_NOW)[0] is False
    aged = _defn(id="caged", schedule=hourly, tz="UTC", created_ts=FIXED_NOW - 2 * HOUR)
    assert is_due(aged, now=FIXED_NOW)[0] is True


def _drain(report_id: str, *, now: float, ticks: int) -> int:
    """Model the runner: sweep `ticks` times at the SAME instant, firing and recording
    whenever the report is due. Returns how many times it fired."""
    fires = 0
    for _ in range(ticks):
        defn = get_report(report_id)
        assert defn is not None
        if is_due(defn, now=now)[0]:
            fires += 1
            record_run(report_id, ok=True, watermark_ts=now)
    return fires


def test_fifty_skipped_windows_fire_exactly_once():
    now = time.time()
    saved = _saved_with_last_run(
        "nap", created_ts=now - 100 * HOUR, last_run_ts=now - 50 * HOUR
    )
    assert _drain(saved.id, now=now, ticks=10) == 1


def test_not_due_again_until_the_next_window_elapses():
    now = time.time()
    saved = save_report(_defn(id="win", created_ts=now - 100 * HOUR))
    record_run(saved.id, ok=True, watermark_ts=now)
    after = get_report(saved.id)
    assert after is not None and after.last_run_ts is not None
    ran_at = after.last_run_ts
    assert is_due(after, now=ran_at)[0] is False
    assert is_due(after, now=ran_at + HOUR - 1)[0] is False
    assert is_due(after, now=ran_at + HOUR)[0] is True


def test_cron_missed_windows_also_fire_once():
    now = time.time()
    saved = _saved_with_last_run(
        "cnap",
        created_ts=now - 200 * HOUR,
        last_run_ts=now - 50 * HOUR,
        schedule=ScheduleDefinition(kind="cron", cron_expr="0 * * * *"),
        tz="UTC",
    )
    assert _drain(saved.id, now=now, ticks=6) == 1


def test_failed_run_records_error_and_leaves_last_run_ts_untouched():
    now = time.time()
    saved = _saved_with_last_run(
        "fail", created_ts=now - 10 * HOUR, last_run_ts=now - 5 * HOUR
    )
    saved.watermark_ts = now - 5 * HOUR
    save_report(saved)

    record_run(saved.id, ok=False, error="provider timed out", watermark_ts=now)
    after = get_report(saved.id)
    assert after is not None
    assert after.last_run_ts == now - 5 * HOUR
    assert after.last_status == "error"
    assert "provider timed out" in after.last_error
    assert after.watermark_ts == now - 5 * HOUR
    assert is_due(after, now=now)[0] is True


def test_successful_run_advances_last_run_ts_and_clears_the_error():
    now = time.time()
    saved = save_report(_defn(id="okrun", created_ts=now - 10 * HOUR))
    record_run(saved.id, ok=False, error="transient")
    record_run(saved.id, ok=True, watermark_ts=now)
    after = get_report(saved.id)
    assert after is not None
    assert after.last_status == "ok"
    assert after.last_error == ""
    assert after.last_run_ts is not None and after.last_run_ts >= now


def test_record_run_for_unknown_id_is_a_noop():
    record_run("does-not-exist", ok=True, watermark_ts=1.0)
    assert load_reports() == []


def test_watermark_is_the_supplied_scope_resolution_time_not_completion():
    now = time.time()
    saved = save_report(_defn(id="wm", created_ts=now - 10 * HOUR))
    resolved_at = now - 300.0
    record_run(saved.id, ok=True, watermark_ts=resolved_at)
    after = get_report(saved.id)
    assert after is not None
    assert after.watermark_ts == resolved_at
    assert after.last_run_ts is not None
    assert after.last_run_ts > resolved_at


def test_watermark_is_left_alone_when_the_runner_supplies_none():
    now = time.time()
    saved = save_report(_defn(id="wmnone", created_ts=now - 10 * HOUR))
    saved.watermark_ts = now - 2 * HOUR
    save_report(saved)
    record_run(saved.id, ok=True)
    after = get_report(saved.id)
    assert after is not None
    assert after.watermark_ts == now - 2 * HOUR

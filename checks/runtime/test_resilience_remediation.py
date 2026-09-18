"""Health-scored remediation engine tests (PLATFORM-RESILIENCE §4).

Pins the deficit→score math (reachable ceilings, unreachable-deficit exclusion), the
dependency-ordered plan, the three stop conditions (target/cost/exhausted), the
cooldown storm-guard, and the ledger.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from gideon.integrations.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    LLMEvent,
    ModelProvider,
)
from gideon.operations.resilience import remediation as rem
from gideon.operations.resilience.remediation import Deficit, RemediationJob
from gideon.security.guardrails.model_call import ModelCallGuard


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate the doctor/ ledger + jobs.json under tmp, and snapshot/restore the job
    registry so test jobs don't leak."""
    monkeypatch.setattr(
        "gideon.operations.resilience.remediation.config_dir", lambda: tmp_path
    )
    saved = dict(rem._JOBS)
    yield
    rem._JOBS.clear()
    rem._JOBS.update(saved)


def test_penalty_is_capped_at_max_penalty():
    d = Deficit(key="k", count=1000, weight=1.0, max_penalty=10.0)
    assert d.penalty == 10.0


def test_health_score_subtracts_reachable_penalties():
    ds = [
        Deficit(key="a", count=5, weight=1.0, max_penalty=20.0),
        Deficit(key="b", count=10, weight=2.0, max_penalty=10.0),
    ]
    assert rem.health_score(ds) == 85.0


def test_unreachable_deficit_excluded_from_score():
    ds = [
        Deficit(key="a", count=10, weight=1.0, max_penalty=20.0, reachable=False),
        Deficit(key="b", count=3, weight=1.0, max_penalty=20.0),
    ]
    assert rem.health_score(ds) == 97.0


def test_health_score_clamped():
    ds = [Deficit(key="a", count=999, weight=1.0, max_penalty=200.0)]
    assert rem.health_score(ds) == 0.0


def test_ordered_respects_after_edges():
    a = RemediationJob(id="a", title="a", run=lambda: "a", after=("b",))
    b = RemediationJob(id="b", title="b", run=lambda: "b")
    ordered = rem._ordered([a, b])
    assert [j.id for j in ordered].index("b") < [j.id for j in ordered].index("a")


def test_ordered_tolerates_cycle():
    a = RemediationJob(id="a", title="a", run=lambda: "a", after=("b",))
    b = RemediationJob(id="b", title="b", run=lambda: "b", after=("a",))
    ordered = rem._ordered([a, b])
    assert {j.id for j in ordered} == {"a", "b"}


def _stub_deficits(monkeypatch, deficits):
    monkeypatch.setattr(rem, "measure_deficits", lambda: deficits)


def test_run_stops_when_already_healthy(monkeypatch):
    _stub_deficits(
        monkeypatch, [Deficit(key="a", count=0, weight=1.0, max_penalty=10.0)]
    )
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert result.stopped_reason == "target_score already met"
    assert result.jobs == []


def test_run_executes_job_and_improves_score(monkeypatch):
    ran = {"n": 0}

    def _job():
        ran["n"] += 1
        return "fixed"

    rem.register_job(
        RemediationJob(id="fix.a", title="Fix A", run=_job, fixes_deficit="a")
    )
    calls = {"n": 0}

    def _measure():
        calls["n"] += 1
        if calls["n"] == 1:
            return [
                Deficit(key="a", count=20, weight=1.0, max_penalty=20.0, job_id="fix.a")
            ]
        return [Deficit(key="a", count=0, weight=1.0, max_penalty=20.0, job_id="fix.a")]

    monkeypatch.setattr(rem, "measure_deficits", _measure)
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 1
    assert result.score_before == 80.0 and result.score_after == 100.0
    assert result.stopped_reason == "target_score reached"
    assert result.jobs[0]["status"] == "ok"


def test_run_skips_unreachable_deficit_job(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.b",
            title="Fix B",
            run=lambda: ran.__setitem__("n", 1) or "x",
            fixes_deficit="b",
        )
    )
    _stub_deficits(
        monkeypatch,
        [
            Deficit(
                key="b",
                count=50,
                weight=1.0,
                max_penalty=20.0,
                reachable=False,
                job_id="fix.b",
            )
        ],
    )
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 0
    assert result.stopped_reason == "target_score already met"


def test_run_respects_cooldown(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.c",
            title="Fix C",
            run=lambda: ran.__setitem__("n", ran["n"] + 1) or "x",
            fixes_deficit="c",
            cooldown_hours=24.0,
        )
    )
    _stub_deficits(
        monkeypatch,
        [Deficit(key="c", count=20, weight=1.0, max_penalty=20.0, job_id="fix.c")],
    )
    rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
    assert ran["n"] == 1
    result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0 + 3600)
    assert ran["n"] == 1
    assert any(j["status"] == "skipped_cooldown" for j in result.jobs)


def test_dry_run_does_not_execute_or_change_score(monkeypatch):
    ran = {"n": 0}
    rem.register_job(
        RemediationJob(
            id="fix.d",
            title="Fix D",
            run=lambda: ran.__setitem__("n", 1) or "x",
            fixes_deficit="d",
        )
    )
    _stub_deficits(
        monkeypatch,
        [Deficit(key="d", count=20, weight=1.0, max_penalty=20.0, job_id="fix.d")],
    )
    result = rem.run_remediation(
        target_score=90, max_cost_usd=1.0, now=1000.0, dry_run=True
    )
    assert ran["n"] == 0
    assert result.score_after == result.score_before
    assert all(j["status"] == "would_run" for j in result.jobs)


def test_ledger_written_and_read_back(monkeypatch):
    rem.register_job(
        RemediationJob(id="fix.e", title="Fix E", run=lambda: "done", fixes_deficit="e")
    )
    calls = {"n": 0}

    def _measure():
        calls["n"] += 1
        return [
            Deficit(
                key="e",
                count=(20 if calls["n"] == 1 else 0),
                weight=1.0,
                max_penalty=20.0,
                job_id="fix.e",
            )
        ]

    monkeypatch.setattr(rem, "measure_deficits", _measure)
    rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1234.0)
    runs = rem.recent_runs()
    assert len(runs) == 1
    assert runs[0]["ts"] == 1234.0
    assert runs[0]["stopped_reason"] in ("target_score reached", "plan exhausted")


def test_builtin_deterministic_jobs_registered():
    ids = {j.id for j in rem.all_jobs()}
    assert {
        "serving-fs.prune-orphans",
        "skills.age",
        "knowledge.reindex-embeddings",
    } <= ids
    for j in rem.all_jobs():
        if j.id in (
            "serving-fs.prune-orphans",
            "skills.age",
            "knowledge.reindex-embeddings",
        ):
            assert j.lane == "deterministic"


_ABSORBED = {
    "memory.rebuild-fts": "memory_fts_desync",
    "memory.prune-history": "history_over_retention",
    "sel.prune": "sel_prunable_entries",
    "skills.age": "skill_aging_due",
}


def test_absorbed_maintenance_jobs_registered():
    """Every maintenance pass retired from the heartbeat has a registered engine job in
    the deterministic ($0) lane.

    Load-bearing since PR2-8 deleted `_legacy_maintenance`: the heartbeat no longer keeps a
    duplicate copy of these passes, so if one disappears from this registry there is nothing
    left running it and the loss is silent (an absent prune is invisible by nature)."""
    jobs = {j.id: j for j in rem.all_jobs()}
    for job_id, deficit_key in _ABSORBED.items():
        assert job_id in jobs, f"{job_id} is not registered"
        assert jobs[job_id].lane == "deterministic"
        assert jobs[job_id].fixes_deficit == deficit_key


def test_every_job_bearing_deficit_can_be_scheduled_alone(tmp_path, monkeypatch):
    """🔴 The rail that catches a registered-but-unschedulable job.

    ``run_remediation`` bails out while ``score_before >= target_score``, so a deficit whose
    whole penalty fits inside ``100 − target`` can NEVER schedule its job — at any backlog.
    Two shipped deficits sat exactly on that line (`orphan_locks`, `skill_aging_due`, both
    ``max_penalty=10.0`` against the default target 90: 1000 stale skills still scored 90.0
    and stopped with "target_score already met"). Raise the ceiling for a new deficit; never
    lower this floor."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    by_key = {d.key: d for d in rem.measure_deficits()}
    job_deficits = {j.fixes_deficit for j in rem.all_jobs() if j.fixes_deficit}
    assert job_deficits <= set(by_key), (
        f"deficit(s) {sorted(job_deficits - set(by_key))} are declared by a job but were not "
        f"measured at all — their measure branch is swallowing an exception"
    )
    for key in sorted(job_deficits):
        d = by_key[key]
        assert d.max_penalty > rem._MIN_SCHEDULABLE_PENALTY, (
            f"deficit {key!r} caps at {d.max_penalty} penalty points, which never drops the "
            f"score below the default target — its job can never be scheduled by it alone"
        )


def test_skills_tampered_deficit_is_a_detector_not_a_job(tmp_path, monkeypatch):
    """`verify_skill_integrity` is finally SCHEDULED — as a measured deficit on every engine
    pass and Doctor read — but deliberately job-less and unreachable: no job can un-tamper a
    skill, and re-baselining a mutated one would launder the tamper. So it must never burn
    budget nor depress a score the engine cannot improve."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    tampered = [d for d in rem.measure_deficits() if d.key == "skills_tampered"]
    assert (
        tampered
    ), "skill-integrity is not measured — verify_skill_integrity is unscheduled"
    d = tampered[0]
    assert d.reachable is False and d.job_id == ""
    assert rem.health_score([d]) == 100.0
    assert not [j for j in rem.all_jobs() if j.fixes_deficit == "skills_tampered"]


def test_history_prune_job_deletes_expired_files_and_their_index_rows(
    tmp_path, monkeypatch
):
    """The job does the WORK: expired daily-history files are gone, and so are the FTS rows
    that would otherwise keep returning snippets for deleted files."""
    from datetime import datetime, timedelta

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.cognition.memory import MemoryJournal, workspace_dir

    # No explicit workspace: the JOB builds MemoryJournal() from config_dir(), and passing one
    mem = MemoryJournal()
    mem.init()
    hist = workspace_dir() / "memory" / "history"
    old = hist / f"{datetime.now().date() - timedelta(days=400)}.md"
    fresh = hist / f"{datetime.now().date()}.md"
    for p in (old, fresh):
        p.write_text("# day\n\n#### 09:00\nstuff\n", encoding="utf-8")
        mem._index_file(p, p.read_text(encoding="utf-8"))

    assert mem.count_history_over_retention(365) == 1
    detail = rem._job_prune_history()

    assert "1 history file" in detail
    assert not old.exists() and fresh.exists()
    assert mem.count_history_over_retention(365) == 0
    assert str(old) not in dict(_indexed_rows(mem))
    assert str(fresh) in dict(_indexed_rows(mem))


def _indexed_rows(mem):
    conn = mem._get_db()
    try:
        return list(conn.execute("SELECT path, content FROM memory_fts"))
    finally:
        conn.close()


def test_fts_rebuild_job_reconciles_out_of_band_edits(tmp_path, monkeypatch):
    """The job does the WORK: a memory file edited outside the store API is measured as
    desync and the index matches disk afterwards."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.cognition.memory import MemoryJournal, workspace_dir

    mem = MemoryJournal()
    mem.init()
    mem.write_preferences("# User Preferences\n\nlikes tea\n")
    mem.rebuild_index()
    assert mem.fts_desync_count() == 0

    prefs = workspace_dir() / "memory" / "preferences.md"
    prefs.write_text("# User Preferences\n\nedited by hand\n", encoding="utf-8")
    assert mem.fts_desync_count() == 1

    detail = rem._job_rebuild_memory_fts()

    assert "FTS index rebuilt" in detail
    assert mem.fts_desync_count() == 0
    assert dict(_indexed_rows(mem))[str(prefs)] == prefs.read_text(encoding="utf-8")


def test_sel_prune_job_removes_exactly_what_it_measured(tmp_path, monkeypatch):
    """The job does the WORK: aged entries are gone, fresh ones survive, and the measured
    deficit equals what the prune removed (shared plan, so they can never disagree)."""
    import json
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.security.sel import sel

    log = sel()
    log.log_api_access(caller="t", operation="fresh", outcome="ok")
    aged = (datetime.now(tz=timezone.utc) - timedelta(days=400)).isoformat()
    with log._path.open("a", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"timestamp": aged, "event": "old", "n": i}) + "\n")

    assert log.count_prunable() == 5
    detail = rem._job_prune_sel()

    assert "pruned 5" in detail
    assert log.count_prunable() == 0
    body = log._path.read_text(encoding="utf-8")
    assert "fresh" in body and '"event": "old"' not in body


def _seeded_store(tmp_path, n=3):
    from gideon.cognition.knowledge.store import KnowledgeStore

    store = KnowledgeStore(tmp_path / "k.db")
    for i in range(n):
        store.create_typed_item(item_type="note", title=f"N{i}", content=f"body {i}")
    assert store.count_items_missing_embedding() == n
    return store


def _bind(monkeypatch, store, embed):
    """Bind what the job resolves: the store, and an embedder OBJECT.

    The bug was that the job passed a bare `Callable[[str], vector]`, which `reembed_all`
    cannot use — so these fakes are deliberately the real `KnowledgeStore` and a real
    `UnifiedEmbedder`, not stubs. A stub accepting anything would have passed against the
    broken code too.
    """
    from gideon.cognition.knowledge.embedder import UnifiedEmbedder

    monkeypatch.setattr("gideon.cognition.knowledge.get_knowledge_store", lambda: store)
    monkeypatch.setattr(
        "gideon.cognition.knowledge.get_knowledge_embedder",
        lambda: UnifiedEmbedder(embed),
    )


def test_reindex_embeddings_actually_drains_the_backlog(tmp_path, monkeypatch):
    """It reported "re-embedded 0 items" in every install: the embedder it passed was
    unusable AND the count it read was a key `reembed_all` does not return, so a job that
    embedded nothing was indistinguishable from a library with nothing to embed."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [0.1, 0.2, 0.3])

    detail = rem._job_reindex_embeddings()

    assert detail == "re-embedded 3 items"
    assert store.count_items_missing_embedding() == 0


def test_reindex_embeddings_only_touches_items_missing_a_vector(tmp_path, monkeypatch):
    """The deficit is `knowledge_missing_embeddings` and the job is titled "Backfill
    missing knowledge embeddings", so a whole-library re-embed is the wrong scope — it
    re-embeds every item the owner has on a 6-hourly cadence. The count in the message is
    what proves the scope."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [0.1, 0.2, 0.3])
    assert rem._job_reindex_embeddings() == "re-embedded 3 items"

    store.create_typed_item(item_type="note", title="fresh", content="new body")

    assert rem._job_reindex_embeddings() == "re-embedded 1 item"


def test_a_total_reindex_failure_raises_instead_of_reporting_zero(
    tmp_path, monkeypatch
):
    """Raising is the only way this job can say "it did not work": `run_remediation`
    writes `last_success_ts` on any non-raising return, so a clean zero would take the
    job's 6h cooldown while the deficit it claims to fix stayed exactly where it was."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [])

    with pytest.raises(RuntimeError, match="embedded none of"):
        rem._job_reindex_embeddings()

    assert store.count_items_missing_embedding() == 3


def test_a_partial_reindex_reports_the_remainder_and_keeps_its_progress(
    tmp_path, monkeypatch
):
    """A partial pass DID reduce the backlog, so it must not raise — that would discard
    the progress from the ledger and redo the same work next tick. `reembed_all` leaves a
    failed item vector-less rather than corrupt, so the remainder is simply still in the
    backlog, which the message says out loud rather than rounding to success."""
    store = _seeded_store(tmp_path)
    _bind(monkeypatch, store, lambda text: [] if "N1" in text else [0.1, 0.2, 0.3])

    detail = rem._job_reindex_embeddings()

    assert detail == "re-embedded 2 items; 1 still without a vector"
    assert store.count_items_missing_embedding() == 1


def test_reindex_embeddings_skips_cleanly_with_no_embedder(tmp_path, monkeypatch):
    store = _seeded_store(tmp_path)
    monkeypatch.setattr("gideon.cognition.knowledge.get_knowledge_store", lambda: store)
    monkeypatch.setattr(
        "gideon.cognition.knowledge.get_knowledge_embedder", lambda: None
    )

    assert rem._job_reindex_embeddings() == "no embedder bound — skipped"
    assert store.count_items_missing_embedding() == 3


class TestTheSpendCapGovernsMeteredWorkOnly:
    """§55 — the dollar cap is a leash on the JUDGMENT lane, not on maintenance.

    The spend is produced by a real :class:`ModelCallGuard` stream over a scripted
    provider — the same chokepoint every model-backed call in the system charges
    through — so what is measured here is the actual accrual path, not a bookkeeping
    stand-in. The job never names a budget: it spends under whatever run key is
    current, and ``run_remediation`` is what makes that key ``doctor``. That is the
    "accrues to the CORRECT budget" half of the criterion.
    """

    @pytest.fixture(autouse=True)
    def _fresh_meter(self):
        from gideon.security.guardrails import budgets

        budgets.reset_meter()
        yield
        budgets.reset_meter()

    class _ScriptedProvider(ModelProvider):
        """Emits one completed turn carrying a provider-reported cost."""

        def __init__(self, cost_usd: float) -> None:
            self._cost = cost_usd

        async def start(self):  # pragma: no cover - never called by the guard
            pass

        async def shutdown(self):
            pass

        async def stream(self, message):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="diagnosed")
            yield LLMEvent(
                kind=EVENT_COMPLETE,
                input_tokens=10,
                output_tokens=20,
                cost_usd=self._cost,
            )

        async def approve_tool(self, request_id):  # pragma: no cover
            pass

        async def reject_tool(self, request_id):  # pragma: no cover
            pass

        def context_usage_pct(self):
            return 0.0

    @classmethod
    def _spend(cls, dollars: float) -> str:
        guard = ModelCallGuard(
            cls._ScriptedProvider(dollars),
            use_case="doctor",
            provider_name="remediation-test",
            model="test-model",
        )

        async def _drain() -> None:
            async for _ in guard.stream("diagnose"):
                pass

        asyncio.run(_drain())
        return f"charged ${dollars}"

    @staticmethod
    def _doctor_spend() -> float:
        from gideon.security.guardrails.budgets import get_meter

        return get_meter().run_totals("doctor").dollars

    def test_judgment_work_accrues_to_the_doctor_budget_and_stops_at_the_cap(
        self, monkeypatch
    ):
        """ac_1 — metered diagnosis charges ``doctor`` and the second one is refused."""
        ran: list[str] = []

        def _first() -> str:
            ran.append("first")
            return self._spend(0.40)

        def _second() -> str:  # pragma: no cover - reaching this is the failure
            ran.append("second")
            return self._spend(0.40)

        rem.register_job(
            RemediationJob(
                id="judge.a",
                title="Judge A",
                run=_first,
                lane="judgment",
                fixes_deficit="a",
            )
        )
        rem.register_job(
            RemediationJob(
                id="judge.b",
                title="Judge B",
                run=_second,
                lane="judgment",
                after=("judge.a",),
                fixes_deficit="a",
            )
        )
        _stub_deficits(
            monkeypatch,
            [
                Deficit(
                    key="a", count=20, weight=1.0, max_penalty=20.0, job_id="judge.a"
                )
            ],
        )

        result = rem.run_remediation(target_score=90, max_cost_usd=0.25, now=1000.0)

        assert ran == ["first"], "the cap did not stop the second metered job"
        assert self._doctor_spend() == pytest.approx(0.40), (
            "the model call did not accrue to the doctor run budget — "
            "run_remediation's run key is what routes it there"
        )
        assert result.stopped_reason == "max_cost_usd $0.25 reached"
        assert [j["status"] for j in result.jobs] == ["ok", "skipped_budget"]

    def test_zero_cost_maintenance_still_runs_after_the_cap(self, monkeypatch):
        """ac_2 — a free job queued behind an over-budget one is not collateral damage.

        A deterministic job spends nothing, so a dollar cap has no claim on it. The cap
        used to ``break`` the whole plan, which silently made the FTS rebuild and the
        prunes hostages of a metered job that happened to be ordered first.
        """
        ran: list[str] = []

        rem.register_job(
            RemediationJob(
                id="judge.c",
                title="Judge C",
                run=lambda: ran.append("judge") or self._spend(0.40),
                lane="judgment",
                fixes_deficit="a",
            )
        )
        rem.register_job(
            RemediationJob(
                id="judge.d",
                title="Judge D",
                run=lambda: ran.append("judge2") or self._spend(0.40),
                lane="judgment",
                after=("judge.c",),
                fixes_deficit="a",
            )
        )
        rem.register_job(
            RemediationJob(
                id="free.e",
                title="Free E",
                run=lambda: ran.append("free") or "pruned",
                after=("judge.d",),
                fixes_deficit="a",
            )
        )
        _stub_deficits(
            monkeypatch,
            [
                Deficit(
                    key="a", count=20, weight=1.0, max_penalty=20.0, job_id="judge.c"
                )
            ],
        )

        result = rem.run_remediation(target_score=90, max_cost_usd=0.25, now=1000.0)

        assert ran == ["judge", "free"], "free maintenance was blocked by the cap"
        assert result.stopped_reason == "max_cost_usd $0.25 reached"
        by_id = {j["id"]: j["status"] for j in result.jobs}
        assert by_id == {"judge.c": "ok", "judge.d": "skipped_budget", "free.e": "ok"}
        assert self._doctor_spend() == pytest.approx(0.40)


_ABSORBED["inbox.maintenance"] = "inbox_maintenance_backlog"


class TestInboxMaintenanceIsAnEngineJob:
    """Inbox retention/dismissed pruning as a measured engine job (#37).

    The inbox used to run it off a private 6-hour timer inside its own poll loop, where it
    was unmeasured, invisible on the Doctor panel, un-runnable on demand and indifferent to
    the remediation switch. Every test here drives the REAL engine against the REAL live
    service the gateway binds, because the failure this replaces is exactly "a second copy
    of the pass, on its own cadence, that nobody can see".
    """

    @staticmethod
    def _live(tmp_path, monkeypatch, *, expired: int = 0, fresh: int = 0) -> object:
        """The InboxService this process is running, wired the way the gateway wires it."""
        from types import SimpleNamespace

        from gideon.integrations.inbox import InboxItem, InboxState, InboxStore
        from gideon.integrations.inbox_providers import native_source
        from gideon.integrations.inbox_service import InboxService

        store = InboxStore(tmp_path / "inbox.json")
        for n in range(expired):
            store.items[f"C1_old{n}"] = InboxItem(
                id=f"C1_old{n}",
                channel="C1",
                channel_name="#general",
                thread_ts=None,
                message="old",
                sender_id="U2",
                sender_name="Sam",
                created_at=time.time() - 200 * 86400,
            )
        for n in range(fresh):
            store.items[f"C1_new{n}"] = InboxItem(
                id=f"C1_new{n}",
                channel="C1",
                channel_name="#general",
                thread_ts=None,
                message="new",
                sender_id="U2",
                sender_name="Sam",
                created_at=time.time(),
            )
        svc = InboxService(state=InboxState(tmp_path / "state.json"), store=store)
        monkeypatch.setattr(
            native_source, "_dashboard_state", SimpleNamespace(_inbox_svc=svc)
        )
        return svc

    @staticmethod
    def _only_inbox_deficit(monkeypatch):
        """Score on the REAL inbox probe alone, so unrelated subsystems can't move it."""
        real = rem.measure_deficits
        monkeypatch.setattr(
            rem,
            "measure_deficits",
            lambda: [d for d in real() if d.key == "inbox_maintenance_backlog"],
        )

    def test_the_job_is_in_the_builtin_census(self):
        job = {j.id: j for j in rem.all_jobs()}.get("inbox.maintenance")
        assert job is not None, "inbox maintenance is not registered with the engine"
        assert job.lane == "deterministic"
        assert job.fixes_deficit == "inbox_maintenance_backlog"

    def test_the_backlog_is_measured_from_the_live_service(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        svc = self._live(tmp_path, monkeypatch, expired=3, fresh=2)
        svc.state.dismissed.update({"C1_1", "C1_" + str(time.time())})
        measured = {d.key: d for d in rem.measure_deficits()}
        assert "inbox_maintenance_backlog" in measured
        d = measured["inbox_maintenance_backlog"]
        assert d.count == 4 and d.job_id == "inbox.maintenance"
        assert d.max_penalty > rem._MIN_SCHEDULABLE_PENALTY

    def test_a_backlog_runs_the_job_on_the_LIVE_service(self, tmp_path, monkeypatch):
        """The job must prune the running service's store — not a second InboxService of
        its own, whose pruning the live one would overwrite on its next save."""
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        svc = self._live(tmp_path, monkeypatch, expired=12, fresh=1)
        self._only_inbox_deficit(monkeypatch)
        result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
        rows = {j["id"]: j for j in result.jobs}
        assert rows["inbox.maintenance"]["status"] == "ok"
        assert "12" in rows["inbox.maintenance"]["detail"]
        assert rows["inbox.maintenance"]["cost"] == 0.0
        assert len(svc.inbox.items) == 1, "the live service's backlog was not pruned"
        assert result.score_after == 100.0
        assert rem.recent_runs()[0]["jobs"][0]["id"] == "inbox.maintenance"

    def test_no_backlog_skips_the_job(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        svc = self._live(tmp_path, monkeypatch, fresh=3)
        svc.state.dismissed.add("C1_" + str(time.time()))
        real = rem.measure_deficits
        monkeypatch.setattr(
            rem,
            "measure_deficits",
            lambda: [d for d in real() if d.key == "inbox_maintenance_backlog"]
            + [Deficit(key="other", count=20, weight=1.0, max_penalty=20.0)],
        )
        result = rem.run_remediation(target_score=90, max_cost_usd=1.0, now=1000.0)
        assert result.stopped_reason == "plan exhausted"
        assert "inbox.maintenance" not in {j["id"] for j in result.jobs}
        assert len(svc.inbox.items) == 3

    def test_the_job_is_inert_with_no_live_service(self, tmp_path, monkeypatch):
        """Headless (no gateway): the job must report, not construct a service of its own."""
        from gideon.integrations.inbox_providers import native_source

        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        monkeypatch.setattr(native_source, "_dashboard_state", None)
        assert (
            rem._job_inbox_maintenance()
            == "no live inbox service — nothing to maintain"
        )

    @pytest.mark.asyncio
    async def test_the_global_switch_gates_it(self, tmp_path, monkeypatch):
        """Disabled engine → the pass does not run at all. There is no private timer left
        to run it anyway, which is the point of the vacuity leg below."""
        from types import SimpleNamespace

        from gideon.core.config.loader import AppConfig
        from gideon.integrations.action_providers import remediation_provider as P
        from gideon.integrations.action_providers.base import ActionContext

        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        svc = self._live(tmp_path, monkeypatch, expired=12)
        self._only_inbox_deficit(monkeypatch)

        def _cfg(enabled: bool):
            return SimpleNamespace(
                resilience=SimpleNamespace(
                    remediation=SimpleNamespace(
                        enabled=enabled,
                        target_score=90,
                        max_cost_usd=1.0,
                        idle_minutes_healthy=60,
                        tick_minutes_degraded=5,
                    )
                )
            )

        monkeypatch.setattr(
            AppConfig, "load", staticmethod(lambda *a, **k: _cfg(False))
        )
        provider = P.SelfRemediationActionProvider()
        result = await provider.execute({}, ActionContext(event="cron"))
        assert result.success and len(svc.inbox.items) == 12

        monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: _cfg(True)))
        assert (await provider.execute({}, ActionContext(event="cron"))).success
        assert len(svc.inbox.items) == 0, "the enabled engine did not run the pass"

    def test_the_run_now_endpoint_reaches_the_live_service(self, tmp_path, monkeypatch):
        """POST /api/doctor/remediation/run over the REAL handler — the on-demand control."""
        import asyncio
        import json as _json

        from aiohttp.test_utils import make_mocked_request

        from gideon.interfaces.dashboard.handlers import doctor as doctor_h

        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        svc = self._live(tmp_path, monkeypatch, expired=12)
        self._only_inbox_deficit(monkeypatch)

        req = make_mocked_request("POST", "/api/doctor/remediation/run")

        async def _body():
            return {"confirm": True}

        req.json = _body  # type: ignore[method-assign]
        resp = asyncio.run(doctor_h.api_doctor_remediation_run(req))
        payload = _json.loads(resp.body.decode())
        assert resp.status == 200
        assert {j["id"]: j["status"] for j in payload["jobs"]}[
            "inbox.maintenance"
        ] == "ok"
        assert len(svc.inbox.items) == 0

    def test_the_plan_preview_lists_it(self, tmp_path, monkeypatch):
        """GET /api/doctor/remediation — diagnostics show the pending pass instead of a
        timer nobody can see."""
        import asyncio
        import json as _json

        from aiohttp.test_utils import make_mocked_request

        from gideon.interfaces.dashboard.handlers import doctor as doctor_h

        monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
        self._live(tmp_path, monkeypatch, expired=12)
        self._only_inbox_deficit(monkeypatch)
        req = make_mocked_request("GET", "/api/doctor/remediation")
        payload = _json.loads(
            asyncio.run(doctor_h.api_doctor_remediation(req)).body.decode()
        )
        assert {d["key"] for d in payload["deficits"]} == {"inbox_maintenance_backlog"}
        assert {j["id"]: j["status"] for j in payload["plan"]} == {
            "inbox.maintenance": "would_run"
        }

"""Doctor framework doctrine + probe tests (PLATFORM-RESILIENCE §1).

The doctrine (§1.3) is the whole point of the tier ladder, so it is pinned here as
executable invariants:

* a tier-3 capability failure degrades ONLY that capability — never ``core_ok``,
  never ``restart_suggested`` (§1.3.1);
* a tier-2 (cheap-RPC) failure short-circuits the tier-3 packs and IS the only
  failure that suggests a restart;
* a probe that raises becomes an ``ok=False`` row, never a 500 (the AUTO-R15
  framework invariant);
* secrets are masked out of ``detail``/``evidence`` before they leave a probe.

Success-criterion #1 (gateway healthy, a capability dead → core OK, that
capability failed at tier 3, no restart) is asserted directly.
"""

from __future__ import annotations

import os

import pytest

from gideon.operations.resilience import doctor
from gideon.operations.resilience.doctor import (
    DoctorContext,
    Probe,
    ProbeResult,
    Tier,
    run_capability,
    run_doctor,
)


def _ok(cap: str, tier: Tier, detail: str = "ok") -> Probe:
    async def _run(ctx: DoctorContext) -> ProbeResult:
        return ProbeResult(ok=True, detail=detail)

    return Probe(f"{cap}.ok.{int(tier)}", cap, tier, _run, f"{cap} ok")


def _fail(cap: str, tier: Tier, detail: str = "boom") -> Probe:
    async def _run(ctx: DoctorContext) -> ProbeResult:
        return ProbeResult(ok=False, detail=detail)

    return Probe(f"{cap}.fail.{int(tier)}", cap, tier, _run, f"{cap} fail")


def _raiser(cap: str, tier: Tier, exc: Exception) -> Probe:
    async def _run(ctx: DoctorContext) -> ProbeResult:
        raise exc

    return Probe(f"{cap}.raise.{int(tier)}", cap, tier, _run, f"{cap} raise")


@pytest.mark.asyncio
async def test_capability_failure_never_marks_core_or_suggests_restart():
    """§1.3.1 — a tier-3 capability failure degrades ONLY that capability's row.

    Success-criterion #1: gateway healthy (core tiers pass) but a capability dead →
    core_ok stays True, restart is NOT suggested, only that capability is 'worst'.
    """
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        _fail("local-models", Tier.CAPABILITY, "ollama dead; HF cache wiped"),
        _ok("memory", Tier.CAPABILITY),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)

    assert rep["core_ok"] is True
    assert rep["restart_suggested"] is False
    assert rep["ok"] is False
    assert rep["worst"] == "local-models"
    assert rep["capabilities"]["local-models"]["ok"] is False
    assert rep["capabilities"]["memory"]["ok"] is True
    assert rep["skipped_capabilities"] == []


@pytest.mark.asyncio
async def test_cheap_rpc_failure_short_circuits_and_suggests_restart():
    """A tier-2 failure is a CORE failure: tier-3 packs are skipped and restart is
    the (only) suggested remedy."""
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _fail("core", Tier.CHEAP_RPC, "status snapshot unreadable"),
        _ok("memory", Tier.CAPABILITY),
        _fail("local-models", Tier.CAPABILITY),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)

    assert rep["core_ok"] is False
    assert rep["restart_suggested"] is True
    assert "memory" not in rep["capabilities"]
    assert "local-models" not in rep["capabilities"]
    assert set(rep["skipped_capabilities"]) == {"memory", "local-models"}


@pytest.mark.asyncio
async def test_socket_failure_short_circuits_without_restart_flag():
    """A tier-1 (socket) failure is a core failure and short-circuits, but only the
    cheap-RPC tier sets restart_suggested — a socket failure does not."""
    probes = [
        _ok("core", Tier.PROCESS),
        _fail("core", Tier.SOCKET, "port not connectable"),
        _ok("core", Tier.CHEAP_RPC),
        _ok("memory", Tier.CAPABILITY),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)

    assert rep["core_ok"] is False
    assert rep["restart_suggested"] is False
    assert rep["skipped_capabilities"] == ["memory"]


@pytest.mark.asyncio
async def test_probe_that_raises_becomes_ok_false_never_propagates():
    """The AUTO-R15 framework invariant: a probe exception is an ok=False row, not a
    raised error — run_doctor must never propagate a probe's bug."""
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        _raiser("memory", Tier.CAPABILITY, RuntimeError("db handle exploded")),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)

    row = rep["capabilities"]["memory"]["probes"][0]
    assert row["ok"] is False
    assert "RuntimeError" in row["detail"]
    assert rep["core_ok"] is True


@pytest.mark.asyncio
async def test_secrets_masked_in_detail_and_evidence():
    """A credential in a probe's human output is run through ``redact()`` before it
    leaves the probe. Uses an AWS-key-shaped token — a pattern ``redact()``
    genuinely masks — to prove the masking is actually wired (not that redact()
    covers every shape; that's security.py's contract, not the Doctor's)."""

    async def _leaky(ctx: DoctorContext) -> ProbeResult:
        secret = "AKIA" + "B" * 16
        return ProbeResult(ok=False, detail=f"auth failed with {secret}")

    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        Probe(
            "model-providers.leak", "model-providers", Tier.CAPABILITY, _leaky, "leaky"
        ),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)
    detail = rep["capabilities"]["model-providers"]["probes"][0]["detail"]
    assert "AKIABBBB" not in detail
    assert "REDACTED" in detail


@pytest.mark.asyncio
async def test_all_healthy_report_is_ok():
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        _ok("memory", Tier.CAPABILITY),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)
    assert rep["ok"] is True and rep["core_ok"] is True
    assert rep["worst"] == "" and rep["restart_suggested"] is False


@pytest.mark.asyncio
async def test_run_capability_unknown_is_flagged():
    result = await run_capability("nope", DoctorContext())
    assert result["unknown"] is True and result["ok"] is True and result["probes"] == []


def test_doctor_error_codes_ride_the_registry():
    """The doctor's wire errors are registry-governed, not hand-built dicts.

    ``unknown_capability`` and ``doctor_disabled`` used to be literal
    ``{"error": {...}}`` payloads — codes the append-only rail could not see, so
    nothing protected them from being reworded or dropped. Both must be
    registered, and the handlers must emit them via ``json_error`` (asserted
    against the handler source: any reintroduced literal envelope dict reds this).
    """
    import json
    from pathlib import Path

    import gideon.interfaces.dashboard.handlers.doctor as doctor_handlers
    from gideon.http_errors import HTTP_ERROR_CODES, json_error

    assert "doctor_disabled" in HTTP_ERROR_CODES
    assert "unknown_capability" in HTTP_ERROR_CODES

    resp = json_error(
        "unknown_capability", message="No such capability: nope.", status=404
    )
    body = json.loads(resp.body)
    assert resp.status == 404
    assert body["error"]["code"] == "unknown_capability"
    assert body["error"]["message"] == "No such capability: nope."

    source = Path(doctor_handlers.__file__).read_text(encoding="utf-8")
    assert (
        '"error": {' not in source
    ), "doctor handlers must emit errors via json_error only"


@pytest.mark.asyncio
async def test_run_capability_runs_only_that_capability(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_PROBES",
        [_ok("memory", Tier.CAPABILITY), _fail("channels", Tier.CAPABILITY)],
    )
    result = await run_capability("channels", DoctorContext())
    assert result["capability"] == "channels" and result["ok"] is False
    assert [p["capability"] for p in result["probes"]] == ["channels"]


@pytest.mark.asyncio
async def test_builtin_probes_registered():
    ids = {p.id for p in doctor.all_probes()}
    assert {
        "gateway.process",
        "gateway.socket",
        "gateway.status",
        "memory.store",
        "channels.transports",
        "local-models.providers",
        "apps.backends",
        "serving-fs.dist",
        "model-providers.health",
    } <= ids


@pytest.mark.asyncio
async def test_socket_probe_skips_when_no_port():
    """A standalone doctor run with no bound port is not a socket failure."""
    res = await doctor._probe_gateway_socket(DoctorContext(port=0))
    assert res.ok is True and "no gateway port" in res.detail


@pytest.mark.asyncio
async def test_memory_probe_fresh_home_is_ok(tmp_path, monkeypatch):
    """No memory.db yet (fresh install) → ok with a 'fresh install' note, not a fail."""
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    res = await doctor._probe_memory(DoctorContext(home=tmp_path))
    assert res.ok is True
    assert res.evidence["db_present"] is False


@pytest.mark.asyncio
async def test_memory_probe_detects_faiss_desync(tmp_path):
    """faiss ids.json count disagreeing with embedded row count → a failed row."""
    import json
    import sqlite3

    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE episodic_memories (id TEXT, is_deleted INTEGER, embedding BLOB)"
    )
    conn.execute("INSERT INTO episodic_memories VALUES ('a', 0, X'00')")
    conn.execute("INSERT INTO episodic_memories VALUES ('b', 0, X'00')")
    conn.commit()
    conn.close()
    (tmp_path / "memory.ids.json").write_text(json.dumps(["a"]), encoding="utf-8")

    res = await doctor._probe_memory(DoctorContext(home=tmp_path))
    assert res.ok is False
    assert "desync" in res.detail
    assert res.evidence["embedded_count"] == 2 and res.evidence["faiss_ids"] == 1


@pytest.mark.asyncio
async def test_serving_fs_probe_flags_copy_shadowing_symlink(tmp_path, monkeypatch):
    """A real-directory static/dist copy (not a symlink) is the stale-SPA bug-class
    and must be flagged."""
    import gideon

    fake_pkg = tmp_path / "pkg"
    (fake_pkg / "static" / "dist").mkdir(parents=True)
    (fake_pkg / "static" / "dist" / "index.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    monkeypatch.setattr(gideon, "__file__", str(fake_pkg / "__init__.py"))

    res = await doctor._probe_serving_fs(DoctorContext(home=tmp_path))
    assert res.ok is False
    assert res.evidence["dist"]["kind"] == "copy"
    assert "stale SPA" in res.detail


@pytest.mark.asyncio
async def test_the_inventory_probe_reports_unclaimed_state(tmp_path):
    """🔴 THE DEFECT this probe closes. `durability.inventory.audit_home()` is the guard that "keeps
    the manifest honest … which is precisely how nine directories silently escaped backup before the
    inventory existed" — and it had **no runtime caller**. Its only invocations were in
    `test_durability_inventory.py`, against a hand-built eight-path fixture, so a store added after
    the manifest was written could not fail it.

    Pointed at a real home it reported 10 unclaimed paths and 5482 undeclared databases, including
    `learning.db` — verified absent from a real archive. A guard that only ever runs against its own
    fixture is testing the fixture.
    """
    import sqlite3

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    (home / "brand_new_store").mkdir()
    (home / "brand_new_store" / "x.json").write_text("{}", encoding="utf-8")
    sqlite3.connect(str(home / "undeclared.db")).close()

    res = await doctor._probe_state_inventory(DoctorContext(home=home))

    assert res.ok is False
    assert "brand_new_store/" in res.evidence["unclaimed"]
    assert "undeclared.db" in res.evidence["undeclared_dbs"]
    assert "NO snapshot" in res.detail


@pytest.mark.asyncio
async def test_the_inventory_probe_passes_a_fully_claimed_home(tmp_path):
    """A home whose every path is declared must pass — otherwise the probe is noise the user learns
    to ignore, and a real gap arrives to an audience that has stopped reading."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    (home / "tasks").mkdir()
    (home / "gateway.log").write_text("noise", encoding="utf-8")

    res = await doctor._probe_state_inventory(DoctorContext(home=home))

    assert res.ok is True, res.detail
    assert res.evidence["unclaimed_count"] == 0


@pytest.mark.asyncio
async def test_the_inventory_probe_is_registered_as_a_CAPABILITY_probe(tmp_path):
    """Registered, not merely defined — the whole finding was a guard nobody called. `CAPABILITY`
    tier because unclaimed state is a coverage gap to surface, not a reason to call the gateway
    down:
    a lower tier would short-circuit the capability packs over an unrelated new file."""
    ids = {p.id: p for p in doctor.all_probes()}
    assert (
        "durability.inventory" in ids
    ), "the probe must be registered, not just defined"
    probe = ids["durability.inventory"]
    assert probe.tier is Tier.CAPABILITY
    assert probe.capability == "durability"


@pytest.mark.asyncio
async def test_the_inventory_probe_CAPS_its_evidence(tmp_path):
    """A `db_container` regression once produced 5478 rows. An unreadable evidence blob is the same
    failure as no evidence, so both lists are capped while the COUNTS stay exact."""
    import sqlite3

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    for i in range(30):
        sqlite3.connect(str(home / f"db{i:02d}.db")).close()

    res = await doctor._probe_state_inventory(DoctorContext(home=home))

    assert len(res.evidence["undeclared_dbs"]) == 20
    assert res.evidence["undeclared_db_count"] == 30


def _staging(home):
    from gideon.cognition.learning.staging import StagingStore

    return StagingStore(home)


def _flush(
    store, outcome: str, *, cadence: str = "per_turn", cost: float = 0.0
) -> None:
    from gideon.cognition.learning.staging import FlushOutcome

    store.record_flush(cadence=cadence, outcome=FlushOutcome(outcome), cost_usd=cost)


async def _memory_row(home):
    """The registered `memory-pipeline` capability row, exactly as the Doctor reports it."""
    report = await doctor.run_capability("memory-pipeline", DoctorContext(home=home))
    assert report[
        "probes"
    ], "the memory-pipeline row must have at least one registered probe"
    return report["probes"][0]


@pytest.mark.asyncio
async def test_memory_pipeline_warns_on_a_flush_ok_streak_with_nothing_produced(
    tmp_path,
):
    """The dead-read signature: passes completing, over and over, producing nothing.

    Written from real `flush_records` rows, and asserted through the registered capability
    row so the WARN is one a user can actually see.
    """
    store = _staging(tmp_path)
    for _ in range(12):
        _flush(store, "flush_ok", cost=0.001)
    store.close()

    row = await _memory_row(tmp_path)

    assert row["ok"] is False, row["detail"]
    assert "consecutive flush_ok" in row["detail"]
    assert row["evidence"]["all_ok_streak"] == 12
    assert row["evidence"]["produced"] == 0


@pytest.mark.asyncio
async def test_memory_pipeline_stays_ok_when_a_pass_actually_produced(tmp_path):
    """VACUITY. The same twelve OK passes must NOT warn once production is real —
    otherwise the streak rule is a clock, not a signal, and would light on every home.
    """
    store = _staging(tmp_path)
    for _ in range(12):
        _flush(store, "flush_ok")
    _flush(store, "flush_produced")
    store.close()

    row = await _memory_row(tmp_path)

    assert row["ok"] is True, row["detail"]
    assert row["evidence"]["all_ok_streak"] == 0
    assert row["evidence"]["produced"] == 1


@pytest.mark.asyncio
async def test_memory_pipeline_stays_ok_over_a_short_quiet_window(tmp_path):
    """VACUITY. A handful of turns that taught nothing is the NORMAL case — the threshold
    is what separates a quiet week from a dead reader, so a run under it must pass."""
    store = _staging(tmp_path)
    for _ in range(3):
        _flush(store, "flush_ok")
    store.close()

    row = await _memory_row(tmp_path)

    assert row["ok"] is True, row["detail"]
    assert row["evidence"]["all_ok_streak"] == 3


@pytest.mark.asyncio
async def test_memory_pipeline_warns_on_an_unconsumed_staging_backlog(tmp_path):
    """Capture works, the drain does not: the atom's "staging backlog" clause.

    Uses the real `stage` writer, so the count is the store's own unconsumed-entry count
    rather than a number this test invented.
    """
    store = _staging(tmp_path)
    for i in range(doctor._MEMORY_BACKLOG_WARN + 5):
        store.stage(cadence="per_turn", kind="lesson", content=f"staged lesson {i}")
    _flush(store, "flush_produced")
    store.close()

    row = await _memory_row(tmp_path)

    assert row["ok"] is False, row["detail"]
    assert "unconsumed" in row["detail"]
    assert row["evidence"]["staging_backlog"] == doctor._MEMORY_BACKLOG_WARN + 5


@pytest.mark.asyncio
async def test_memory_pipeline_warns_on_a_flush_error(tmp_path):
    """A pass that RAISED used to vanish into a debug log. It is a first-class WARN now."""
    store = _staging(tmp_path)
    _flush(store, "flush_error")
    _flush(store, "flush_produced")
    store.close()

    row = await _memory_row(tmp_path)

    assert row["ok"] is False, row["detail"]
    assert "flush error" in row["detail"]
    assert row["evidence"]["errors"] == 1


@pytest.mark.asyncio
async def test_memory_pipeline_reports_the_per_op_cost_split(tmp_path):
    """ "Was it expensive" is one number; "expensive at WHAT" is the question that leads to a
    change. The atom's per-op clause: the cadence split rides along as evidence, dearest
    first."""
    store = _staging(tmp_path)
    _flush(store, "flush_produced", cadence="per_turn", cost=0.02)
    _flush(store, "flush_produced", cadence="session_end", cost=0.50)
    store.close()

    row = await _memory_row(tmp_path)

    by_op = row["evidence"]["cost_by_op"]
    assert [entry["op"] for entry in by_op] == [
        "session_end",
        "per_turn",
    ]
    assert by_op[0]["cost_usd"] == 0.5
    assert row["evidence"]["cost_usd"] == 0.52


@pytest.mark.asyncio
async def test_memory_pipeline_never_CREATES_the_staging_log(tmp_path):
    """Read-only by contract, in the strict sense. `StagingStore` writes its schema on the
    first cursor, so a probe that "just reads" would materialise `learning.db` in the home
    on every Doctor run — a write from the one module that promises never to make one.
    """
    from gideon.cognition.learning.staging import DB_FILE

    row = await _memory_row(tmp_path)

    assert row["ok"] is True, row["detail"]
    assert row["evidence"] == {"staging_log": False}
    assert not (
        tmp_path / DB_FILE
    ).exists(), "the probe opened (and so created) the staging log"


class TestDiagnosticGrammar:
    """req.38 — a count and its noun agree, at zero, one and many.

    Every case here renders a REAL probe or fix against a tmp home seeded to produce
    exactly 0, 1 or N findings. A unit test of the helper alone would pass while a probe
    that never called it kept printing "1 unclaimed path(s)", which is the defect: the
    claim is about what the diagnosis says, not about what a string function can do.
    """

    @staticmethod
    def _home(tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("GIDEON_HOME", str(home))
        monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: home)
        return home

    @staticmethod
    def _undeclared_dbs(home, n: int) -> None:
        import sqlite3

        for i in range(n):
            sqlite3.connect(str(home / f"stray{i:02d}.db")).close()

    @pytest.mark.asyncio
    async def test_state_inventory_agrees_at_zero_one_and_many(
        self, tmp_path, monkeypatch
    ):
        home = self._home(tmp_path, monkeypatch)

        zero = await doctor._probe_state_inventory(DoctorContext(home=home))
        assert zero.evidence["undeclared_db_count"] == 0
        assert zero.ok is True
        assert "(s)" not in zero.detail

        self._undeclared_dbs(home, 1)
        one = await doctor._probe_state_inventory(DoctorContext(home=home))
        assert one.evidence["undeclared_db_count"] == 1
        assert "1 undeclared database" in one.detail
        assert "1 undeclared databases" not in one.detail
        assert "(s)" not in one.detail

        self._undeclared_dbs(home, 4)
        many = await doctor._probe_state_inventory(DoctorContext(home=home))
        assert many.evidence["undeclared_db_count"] == 4
        assert "4 undeclared databases" in many.detail
        assert "(s)" not in many.detail

    @pytest.mark.asyncio
    async def test_the_unclaimed_path_count_agrees_with_its_own_number(
        self, tmp_path, monkeypatch
    ):
        """Both counts in one sentence, on OPPOSING sides of n === 1.

        A sentence that borrowed its sibling's number would still read correctly with
        both counts plural, so the singular is placed on one half at a time.
        """
        home = self._home(tmp_path, monkeypatch)
        (home / "a-stray-file.txt").write_text("x", encoding="utf-8")
        self._undeclared_dbs(home, 3)

        res = await doctor._probe_state_inventory(DoctorContext(home=home))

        assert res.evidence["unclaimed_count"] >= 1
        if res.evidence["unclaimed_count"] == 1:
            assert "1 unclaimed path and" in res.detail
        assert "3 undeclared databases" in res.detail
        assert "(s)" not in res.detail

    @pytest.mark.asyncio
    async def test_crash_artifacts_agree_at_zero_one_and_many(
        self, tmp_path, monkeypatch
    ):
        import time as _t

        from gideon.operations.resilience import crashes

        home = self._home(tmp_path, monkeypatch)

        zero = await doctor._probe_crashes(DoctorContext(home=home))
        assert zero.ok is True and zero.detail == "no crash artifacts"

        crashes.record_crash("turn", RuntimeError("boom"), now=_t.time())
        one = await doctor._probe_crashes(DoctorContext(home=home))
        assert len(one.evidence["crashes"]) == 1
        assert one.detail.startswith("1 recent crash artifact;")
        assert "(s)" not in one.detail

        for i in range(2):
            crashes.record_crash(
                "loop_worker", RuntimeError(f"b{i}"), now=_t.time() + i
            )
        many = await doctor._probe_crashes(DoctorContext(home=home))
        assert len(many.evidence["crashes"]) == 3
        assert many.detail.startswith("3 recent crash artifacts;")
        assert "(s)" not in many.detail

    @pytest.mark.asyncio
    async def test_memory_pipeline_passes_agree_at_zero_one_and_many(
        self, tmp_path, monkeypatch
    ):
        home = self._home(tmp_path, monkeypatch)

        store = _staging(home)
        store.stage(cadence="per_turn", kind="lesson", content="something captured")
        store.close()
        zero = await doctor._probe_memory_pipeline(DoctorContext(home=home))
        assert zero.evidence["passes"] == 0
        assert "0 passes in" in zero.detail
        assert "(es)" not in zero.detail

        store = _staging(home)
        _flush(store, "flush_produced")
        store.close()
        one = await doctor._probe_memory_pipeline(DoctorContext(home=home))
        assert one.evidence["passes"] == 1
        assert "1 pass in" in one.detail
        assert "1 passes" not in one.detail

        store = _staging(home)
        for _ in range(3):
            _flush(store, "flush_produced")
        store.close()
        many = await doctor._probe_memory_pipeline(DoctorContext(home=home))
        assert many.evidence["passes"] == 4
        assert "4 passes in" in many.detail
        assert "(es)" not in many.detail

    @pytest.mark.asyncio
    async def test_memory_pipeline_flush_errors_agree_at_one_and_many(
        self, tmp_path, monkeypatch
    ):
        """The zero case is the clause DISAPPEARING, which is its own correctness claim."""
        home = self._home(tmp_path, monkeypatch)

        store = _staging(home)
        _flush(store, "flush_produced")
        store.close()
        clean = await doctor._probe_memory_pipeline(DoctorContext(home=home))
        assert clean.ok is True and "flush error" not in clean.detail

        store = _staging(home)
        _flush(store, "flush_error")
        store.close()
        one = await doctor._probe_memory_pipeline(DoctorContext(home=home))
        assert one.evidence["errors"] == 1
        assert "1 flush error in" in one.detail
        assert "1 flush errors" not in one.detail

        store = _staging(home)
        for _ in range(2):
            _flush(store, "flush_error")
        store.close()
        many = await doctor._probe_memory_pipeline(DoctorContext(home=home))
        assert many.evidence["errors"] == 3
        assert "3 flush errors in" in many.detail

    def test_the_orphan_prune_fix_agrees_at_zero_one_and_many(
        self, tmp_path, monkeypatch
    ):
        """The confirm-gated fixes render counts too, from the same helper."""
        import time as _t

        from gideon.operations.resilience import fixes

        home = self._home(tmp_path, monkeypatch)
        locks = home / "locks"
        locks.mkdir()

        def _stale(name: str) -> None:
            path = locks / name
            path.write_text("", encoding="utf-8")
            old = _t.time() - 90000
            os.utime(path, (old, old))

        assert fixes._orphan_prune_preview() == (
            "No orphaned locks or rollback leftovers found."
        )

        _stale("a.lock")
        one = fixes._orphan_prune_preview()
        assert "1 stale lock file (>24h old)" in one
        assert "(s)" not in one

        for name in ("b.lock", "c.lock"):
            _stale(name)
        many = fixes._orphan_prune_preview()
        assert "3 stale lock files (>24h old)" in many
        assert "(s)" not in many

        applied = fixes._orphan_prune_apply()
        assert "Removed 3 stale locks" in applied
        assert "reconciled 0 rollback leftovers" in applied
        assert "(s)" not in applied

    def test_no_rendered_diagnostic_string_carries_a_placeholder_plural(self):
        """The rail that keeps the form from coming back.

        Source-level because the claim is about the whole surface, not the handful of
        probes a tmp home can be steered into: a new probe writing "{n} thing(s)" would
        be invisible to every behavioural test above until someone seeded its condition.

        Scanned over the AST's string constants rather than raw lines, so it reads what
        is RENDERED and not the prose that documents the defect — a line-based scan
        flagged this module's own explanation of the bug.
        """
        import ast
        import pathlib
        import re

        from gideon.interfaces.cli import doctor as cli_doctor
        from gideon.operations import resilience

        targets = sorted(pathlib.Path(resilience.__file__).parent.glob("*.py"))
        targets.append(pathlib.Path(cli_doctor.__file__))
        placeholder = re.compile(r"\w\((?:s|es|ies)\)")

        offenders = []
        for path in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            prose = set()
            for node in ast.walk(tree):
                if isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    body = getattr(node, "body", [])
                    if (
                        body
                        and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        prose.add(id(body[0].value))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in prose
                    and placeholder.search(node.value)
                ):
                    offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")
        assert offenders == [], "placeholder plurals: " + "\n".join(offenders)

    def test_the_placeholder_rail_can_actually_see_one(self):
        """VACUITY: the rail above must not be blind to the form it is banning."""
        import ast
        import re

        placeholder = re.compile(r"\w\((?:s|es|ies)\)")
        tree = ast.parse('x = f"{n} unclaimed path(s)"')
        found = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and placeholder.search(node.value)
        ]
        assert found, "the rail would not have caught the original defect"

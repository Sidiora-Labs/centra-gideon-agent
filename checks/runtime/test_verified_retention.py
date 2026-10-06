"""Verified snapshot holds and real retention journals."""

from gideon.operations.durability import retention, service


def snapshot(folder, stamp):
    path = folder / f"gideon-snapshot-{stamp}Z.tar.gz"
    path.write_bytes(b"archive")
    path.with_name(path.name + ".manifest.json").write_text("{}")
    return path


def test_verified_hold_and_real_prune(tmp_path):
    old = snapshot(tmp_path, "20260101T000000")
    middle = snapshot(tmp_path, "20260201T000000")
    newer = snapshot(tmp_path, "20260301T000000")
    snaps = retention.list_snapshots(tmp_path)
    for plan in (
        retention.plan_retention(
            snaps, verified=old.name, daily=1, weekly=0, monthly=0
        ),
        retention.plan_newest(snaps, verified=old.name, keep=1),
    ):
        assert plan.held.name == old.name
        assert {s.name for s in plan.keep} == {old.name, newer.name}
        assert plan.reasons[middle.name]
    dry = retention.apply_retention(
        tmp_path, verified=old.name, daily=1, weekly=0, monthly=0, dry_run=True
    )
    actual = retention.apply_retention(
        tmp_path, verified=old.name, daily=1, weekly=0, monthly=0
    )
    assert dry["pruned"] == actual["pruned"] == [middle.name]
    assert old.exists() and newer.exists()
    assert not middle.with_name(middle.name + ".manifest.json").exists()


def test_zero_budgets_and_absent_verified_do_not_hold(tmp_path):
    old = snapshot(tmp_path, "20260101T000000")
    snapshot(tmp_path, "20260201T000000")
    plan = retention.plan_retention(
        retention.list_snapshots(tmp_path),
        verified=old.name,
        daily=0,
        weekly=0,
        monthly=0,
    )
    assert not plan.keep and plan.held is None
    plan = retention.plan_newest(
        retention.list_snapshots(tmp_path), verified="gone.tar.gz", keep=1
    )
    assert plan.held is None and len(plan.keep) == 1


def test_failed_drill_preserves_prior_verified_and_migrates(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    passed = service.JobResult(
        "restore_drill",
        ok=True,
        detail="verified",
        extra={"snapshot": "old.tar.gz", "databases_checked": 2},
    )
    service.persist_job_result(passed, at=10)
    service.persist_job_result(
        service.JobResult(
            "restore_drill", ok=False, detail="bad", extra={"snapshot": "new.tar.gz"}
        ),
        at=20,
    )
    assert service.last_drill()["ok"] is False
    assert service.last_verified()["archive"] == "old.tar.gz"
    assert service.last_verified()["at"] == 10
    state = service.drill_fields(passed, at=30)
    state = {
        key: value
        for key, value in state.items()
        if not key.startswith("last_verified_")
    }
    service.save_state(state)
    assert service.last_verified()["archive"] == "old.tar.gz"

"""Doctor failures have an operator next step and affect Maintenance health."""

from __future__ import annotations

import asyncio

from gideon.operations.resilience import doctor, remediation
from gideon.operations.resilience.doctor import DoctorContext


def test_real_inventory_failure_has_a_remedy_and_lowers_health(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(home / "workspace"))
    (home / "unclaimed-store").mkdir()
    (home / "unclaimed-store" / "state.json").write_text("{}", encoding="utf-8")

    probes = [
        probe for probe in doctor.all_probes() if probe.id == "durability.inventory"
    ]
    assert len(probes) == 1, "durability inventory probe is not registered"
    report = asyncio.run(doctor.run_doctor(DoctorContext(home=home), probes=probes))
    rows = [row for cap in report["capabilities"].values() for row in cap["probes"]]
    (failed,) = [row for row in rows if not row["ok"]]
    assert failed.get("fix_id") or failed.get("remedy"), failed

    deficits = remediation.measure_deficits()
    assert any(item.key.startswith("check:") for item in deficits), deficits
    assert remediation.health_score(deficits) < 100.0

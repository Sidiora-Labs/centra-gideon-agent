"""The base install carries the IANA data that ``zoneinfo`` needs on minimal Linux."""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_tzdata_is_an_unconditional_runtime_dependency() -> None:
    """A transitive or platform-marked dependency does not protect a minimal Linux install."""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as handle:
        declared = tomllib.load(handle)["project"]["dependencies"]
    matches = [
        Requirement(spec) for spec in declared if Requirement(spec).name == "tzdata"
    ]
    assert len(matches) == 1
    assert (
        matches[0].marker is None
    ), "tzdata must install on minimal Linux, not only one platform"
    assert str(matches[0].specifier) == ">=2024.1"


def test_named_zone_uses_packaged_data_when_system_paths_are_unavailable(
    tmp_path,
) -> None:
    """A fresh interpreter with an empty TZPATH must resolve data from the wheel dependency.

    The invalid-zone negative control matters: a test that only loads one known name could
    pass through a cache or an accidental fallback while the authoring gate classified every
    failure as a missing database.

    The child gets its own `GIDEON_HOME`: this suite's home isolation patches the
    current process, and a subprocess is outside it.
    """
    script = """
import importlib.metadata
import zoneinfo

from gideon.core.timezones import UnknownTimeZone, zone_or_raise

assert zoneinfo.TZPATH == ()
assert importlib.metadata.version("tzdata")
zone = zone_or_raise("America/Los_Angeles")
assert zone.key == "America/Los_Angeles"
try:
    zone_or_raise("Invalid/Timezone")
except UnknownTimeZone:
    pass
else:
    raise AssertionError("a truly invalid zone was accepted")
"""
    env = os.environ.copy()
    env["PYTHONTZPATH"] = ""
    env["GIDEON_HOME"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def _dependency_site_without_tzdata(tmp_path: Path) -> Path:
    """Expose installed real dependencies to a -S child, omitting tzdata."""
    source = Path(sysconfig.get_paths()["purelib"])
    isolated_site = tmp_path / "site-packages-without-tzdata"
    isolated_site.mkdir()
    for entry in source.iterdir():
        name = entry.name.casefold()
        if name == "tzdata" or name.startswith("tzdata-") or entry.suffix == ".pth":
            continue
        (isolated_site / entry.name).symlink_to(
            entry, target_is_directory=entry.is_dir()
        )
    return isolated_site


@pytest.mark.skipif(os.name == "nt", reason="Real terminal input requires a POSIX PTY")
def test_missing_database_routes_all_consumers_to_utc_without_losing_authored_zone(
    tmp_path,
) -> None:
    """A -S interpreter with real deps but no tzdata exercises outage consumers."""
    import pty
    import select

    isolated_site = _dependency_site_without_tzdata(tmp_path)
    script = """
import asyncio
import importlib.util
import site
import sys
import zoneinfo
from datetime import datetime, timezone
from pathlib import Path

site.addsitedir(sys.argv[1])
sys.path.insert(0, sys.argv[2])
assert zoneinfo.TZPATH == ()
assert importlib.util.find_spec("tzdata") is None
try:
    zoneinfo.ZoneInfo("UTC")
except zoneinfo.ZoneInfoNotFoundError:
    pass
else:
    raise AssertionError("real ZoneInfo found data without system paths or tzdata")

from croniter import croniter
from gideon.automation.schedule import ScheduleDefinition, ScheduleJob, _job_tz
from gideon.automation.triggers.arm import next_fire, semantic_spec_issues
from gideon.automation.triggers.calendar import _resolve_zone
from gideon.automation.triggers.models import Trigger
from gideon.cognition.knowledge.report_schedules import _effective_tz
from gideon.cognition.knowledge.research_reports import ReportDefinition, _report_tz
from gideon.core.timezones import TimeZoneDatabaseUnavailable, resolve_zone, zone_or_raise
from gideon.operations.resilience.doctor import DoctorContext, run_capability

try:
    zone_or_raise("America/Los_Angeles", where="config.timezone")
except TimeZoneDatabaseUnavailable as exc:
    assert "database is unavailable" in str(exc)
else:
    raise AssertionError("the real database outage was classified as valid or invalid input")
assert resolve_zone("Asia/Tokyo") is timezone.utc
assert _resolve_zone("Asia/Tokyo") is timezone.utc
job = ScheduleJob(id="j", name="n", schedule=ScheduleDefinition(kind="cron", cron_expr="30 8 * * *"), timezone="Asia/Tokyo")
assert _job_tz(job) is timezone.utc
report = ReportDefinition(id="r", name="n", prompt="p", schedule=ScheduleDefinition(kind="cron", cron_expr="30 8 * * *"), tz="Asia/Tokyo")
assert _report_tz(report) is timezone.utc
assert _effective_tz(report) == "Asia/Tokyo"
now = datetime(2026, 9, 7, 4, 0, tzinfo=timezone.utc).timestamp()
trigger = Trigger(id="t", name="n", kind="clock", enabled=True, spec={"kind": "cron", "expr": "30 8 * * *", "timezone": "Asia/Tokyo"})
expected = croniter("30 8 * * *", datetime.fromtimestamp(now, timezone.utc)).get_next(float)
assert next_fire(trigger, now=now) == expected
issues = semantic_spec_issues("clock", {"kind": "cron", "expr": "30 8 * * *", "timezone": "Asia/Tokyo"})
zone_issues = [issue for issue in issues if issue.path == "spec.timezone"]
assert [issue.severity for issue in zone_issues] == ["warning"]
assert "timezone database is unavailable" in zone_issues[0].message
assert "fires at UTC" in zone_issues[0].message
doctor = asyncio.run(run_capability("scheduling", DoctorContext(home=Path(sys.argv[3]))))
doctor_zone = next(row for row in doctor["probes"] if row["id"] == "scheduling.timezone")
assert "Reinstall Gideon" in doctor_zone["detail"]
assert "gideon setup" not in doctor_zone["detail"]
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONTZPATH"] = ""
    env["TZ"] = "America/New_York"
    env["GIDEON_HOME"] = str(tmp_path / "consumer-home")
    proc = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            script,
            str(isolated_site),
            str(_REPO_ROOT / "runtime"),
            env["GIDEON_HOME"],
        ],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    setup_script = """
import site
import sys
from pathlib import Path
site.addsitedir(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from gideon.interfaces.cli.setup import _setup_timezone, config_path
_setup_timezone()
assert not config_path().exists(), "setup saved an unchecked timezone"
"""
    setup_home = tmp_path / "setup-home"
    setup_env = os.environ.copy()
    setup_env.pop("PYTHONPATH", None)
    setup_env["PYTHONTZPATH"] = ""
    setup_env["TZ"] = "America/New_York"
    setup_env["GIDEON_HOME"] = str(setup_home)
    master, slave = pty.openpty()
    setup_proc = subprocess.Popen(
        [
            sys.executable,
            "-S",
            "-c",
            setup_script,
            str(isolated_site),
            str(_REPO_ROOT / "runtime"),
        ],
        cwd=_REPO_ROOT,
        env=setup_env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    os.write(master, b"Asia/Tokyo\n")
    output = bytearray()
    while True:
        ready, _, _ = select.select([master], [], [], 15)
        if not ready:
            setup_proc.kill()
            raise AssertionError("setup did not finish after actual terminal input")
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
        if setup_proc.poll() is not None and not select.select([master], [], [], 0)[0]:
            break
    os.close(master)
    setup_exit = setup_proc.wait(timeout=15)
    setup_output = output.decode(errors="replace")
    assert setup_exit == 0, setup_output
    assert "IANA timezone" in setup_output
    assert "database is unavailable" in setup_output
    assert "Timezone unchanged" in setup_output
    assert not (setup_home / "config.json").exists()

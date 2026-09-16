import importlib.util
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

from gideon.operations._installer import InstallerPlan, install_argv, installer_name
from gideon.operations.stats import RuntimeCounters, Stats, _uptime


def test_counter_owner_serializes_real_workers_and_detaches_snapshots():
    ledger = RuntimeCounters()

    def record(_):
        for _ in range(1000):
            ledger.add("total_turns", 1)
            ledger.add_cost(0.25)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(8)))
    snapshot = ledger.snapshot()
    assert snapshot["total_turns"] == 8000
    assert ledger.cost() == 2000
    snapshot["total_turns"] = -1
    assert ledger.snapshot()["total_turns"] == 8000
    ledger.add("extension_count", 9)
    before = ledger.started
    ledger.reset()
    assert all(value == 0 for value in ledger.snapshot().values())
    assert ledger.snapshot()["extension_count"] == 0
    assert ledger.started >= before
    assert ledger.cost() == 2000


def test_singleton_is_shared_across_real_threads():
    with ThreadPoolExecutor(max_workers=12) as pool:
        instances = list(pool.map(lambda _: Stats(), range(100)))
    assert all(instance is instances[0] for instance in instances)


def test_uptime_keeps_rounding_and_day_boundaries():
    assert _uptime(3599.49) == "0h 59m"
    assert _uptime(3599.5) == "1h 0m"
    assert _uptime(3 * 86400 + 14 * 3600 + 22 * 60) == "3d 14h 22m"


def test_actual_installer_discovery_and_command_target():
    expected = (
        "uv" if shutil.which("uv") else "pip" if importlib.util.find_spec("pip") else ""
    )
    assert installer_name() == expected
    args = ["--quiet", "--disable-pip-version-check", "gideon-agent-harness"]
    original = args.copy()
    if expected:
        assert install_argv(args) == InstallerPlan(expected, sys.executable).command(
            args
        )
    assert args == original
    assert InstallerPlan("uv", "/workspace/a b/python").command(args) == [
        "uv",
        "pip",
        "install",
        "--python",
        "/workspace/a b/python",
        "--quiet",
        "gideon-agent-harness",
    ]
    assert InstallerPlan("pip", "/workspace/python").command(args) == [
        "/workspace/python",
        "-m",
        "pip",
        "install",
        *args,
    ]

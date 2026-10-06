from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from gideon.core.periodic_sweep import PeriodicSweep


def test_sweep_repeats_until_stopped() -> None:
    ran_twice = threading.Event()
    count = 0

    def sweep() -> None:
        nonlocal count
        count += 1
        if count >= 2:
            ran_twice.set()

    sweeper = PeriodicSweep("test-periodic-sweep-repeats", 0.01, sweep)
    thread = sweeper.start()
    try:
        assert ran_twice.wait(5)
    finally:
        assert sweeper.stop()
    settled = count
    time.sleep(0.05)
    assert count == settled
    assert not thread.is_alive()


def test_long_interval_stops_promptly_and_start_is_idempotent() -> None:
    sweeper = PeriodicSweep("test-periodic-sweep-idempotent", 3600, lambda: None)
    thread = sweeper.start()
    assert sweeper.start() is thread
    assert sweeper.running()
    began = time.monotonic()
    assert sweeper.stop()
    assert time.monotonic() - began < 2
    assert not thread.is_alive()
    assert sweeper.stop()


def test_stopped_sweep_can_restart() -> None:
    sweeper = PeriodicSweep("test-periodic-sweep-restart", 3600, lambda: None)
    first = sweeper.start()
    assert sweeper.stop()
    second = sweeper.start()
    try:
        assert second is not first
        assert second.is_alive()
    finally:
        assert sweeper.stop()


def test_restart_waits_for_a_stopping_generation() -> None:
    entered, release = threading.Event(), threading.Event()

    def sweep() -> None:
        entered.set()
        release.wait(5)

    sweeper = PeriodicSweep("test-periodic-sweep-generation", 0.01, sweep)
    first = sweeper.start()
    assert entered.wait(5)
    assert not sweeper.stop(timeout=0)
    release.set()
    second = sweeper.start()
    try:
        assert first is not second
        assert second.is_alive()
    finally:
        release.set()
        assert sweeper.stop()


def test_callback_failure_does_not_end_sweep() -> None:
    called_again = threading.Event()
    passes = 0

    def sweep() -> None:
        nonlocal passes
        passes += 1
        if passes > 1:
            called_again.set()
            raise RuntimeError("failed pass")
        raise RuntimeError("failed pass")

    sweeper = PeriodicSweep("test-periodic-sweep-failure", 0.01, sweep)
    sweeper.start()
    try:
        assert called_again.wait(5)
        assert sweeper.running()
    finally:
        assert sweeper.stop()


def test_stop_waits_for_an_in_flight_sweep() -> None:
    entered, release = threading.Event(), threading.Event()
    finished = threading.Event()

    def sweep() -> None:
        entered.set()
        release.wait(5)
        finished.set()

    sweeper = PeriodicSweep("test-periodic-sweep-in-flight", 0.01, sweep)
    thread = sweeper.start()
    assert entered.wait(5)
    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        assert sweeper.stop(timeout=2)
        assert finished.is_set()
        assert not thread.is_alive()
    finally:
        release.set()
        timer.join(1)


def test_stop_without_start_is_a_no_op() -> None:
    sweeper = PeriodicSweep("test-periodic-sweep-never-started", 1, lambda: None)
    assert sweeper.stop()


def _isolate_home(monkeypatch, home: Path) -> None:
    for name, path in (
        ("HOME", home / "process-home"),
        ("GIDEON_HOME", home),
        ("GIDEON_WORKSPACE", home / "workspace"),
        ("XDG_CONFIG_HOME", home / "xdg-config"),
        ("XDG_DATA_HOME", home / "xdg-data"),
        ("XDG_CACHE_HOME", home / "xdg-cache"),
        ("TMPDIR", home / "tmp"),
    ):
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("GIDEON_AUTH_MODE", "none")
    monkeypatch.delenv("GIDEON_SKIP_APP_BACKENDS", raising=False)


def _write_backend(home: Path, name: str) -> Path:
    app = home / "apps" / name
    (app / "backend").mkdir(parents=True)
    marker = home / "backend-home.txt"
    (app / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "Gateway cleanup fixture.",
                "backend": {"entryPoint": "backend/server.py", "type": "python"},
                "permissions": {},
            }
        ),
        encoding="utf-8",
    )
    (app / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )
    (app / "backend" / "server.py").write_text(
        "import time\n"
        f"open({str(marker)!r}, 'w', encoding='utf-8').write(__import__('os').getcwd())\n"
        "while True:\n    time.sleep(1)\n",
        encoding="utf-8",
    )
    return marker


@pytest.mark.asyncio
async def test_gateway_cleanup_stops_watchdogs_and_children_across_homes(
    tmp_path, monkeypatch
) -> None:
    from gideon.extensions.apps.backend_runtime import get_backend_supervisor
    from gideon.interfaces.dashboard.server import start_dashboard
    from gideon.operations.durability import history_debounce

    names = (
        "app-backend-watchdog",
        "app-worker-watchdog",
        "model-sidecar-watchdog",
    )
    prior_debouncer = history_debounce.active()
    supervisor = get_backend_supervisor()
    generations = []
    for generation in (1, 2):
        home = tmp_path / f"gateway-{generation}"
        home.mkdir()
        marker = _write_backend(home, "sweep-app")
        _isolate_home(monkeypatch, home)
        before = {
            name: {
                id(thread) for thread in threading.enumerate() if thread.name == name
            }
            for name in names
        }
        from gideon.core.config.loader import AppConfig
        from gideon.engine.session import ConversationDirectory

        sessions = ConversationDirectory(AppConfig.load())
        runner, state = await start_dashboard(sessions=sessions, port=0)
        started = {
            name: [
                thread
                for thread in threading.enumerate()
                if thread.name == name and id(thread) not in before[name]
            ]
            for name in names
        }
        assert all(started.values())
        backend = supervisor.get("sweep-app")
        assert backend is not None and backend.is_alive()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert marker.read_text(encoding="utf-8") == str(home / "apps" / "sweep-app")
        active_debouncer = history_debounce.active()
        if active_debouncer is not None and prior_debouncer is None:
            assert active_debouncer._home == home

        await runner.cleanup()
        await sessions.close_all()
        generations.append(backend)
        assert not backend.is_alive()
        assert state._durability_svc._task is None
        assert state._durability_svc._stopping_task is None
        assert all(
            not thread.is_alive() for threads in started.values() for thread in threads
        )
        assert history_debounce.active() is prior_debouncer
        await asyncio.sleep(0.05)
        assert supervisor.get("sweep-app") is None

    assert generations[0].pid != generations[1].pid


@pytest.mark.parametrize("interval", [0, -1])
def test_interval_must_be_positive(interval: float) -> None:
    with pytest.raises(ValueError, match="interval must be positive"):
        PeriodicSweep("test-periodic-sweep-invalid", interval, lambda: None)

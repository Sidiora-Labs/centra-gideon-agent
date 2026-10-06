from __future__ import annotations

import asyncio
import collections
import io
import json
import logging
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

from gideon.core import log_sinks
from gideon.extensions.apps import code_provenance
from gideon.extensions.apps.backend_runtime import BackendSupervisor
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.apps.native_contract import load_bundle_module
from gideon.extensions.apps.worker_runtime import WorkerSupervisor
from gideon.interfaces.dashboard.handlers.updates import _QueueLogHandler, _RingLogHandler
from gideon.integrations.local_models.sidecar import SidecarRunner
from gideon.operations.child_output import ChildOutput, LINE_MAX_CHARS, TAIL_LINES, relay


def test_loaded_app_reaches_console_file_ring_and_threadsafe_stream(tmp_path):
    async def check():
        old = log_sinks.level()
        log_sinks.set_level(logging.INFO)
        console = io.StringIO()
        ring = collections.deque(maxlen=10)
        queue = asyncio.Queue(maxsize=10)
        file = tmp_path / "gateway.log"
        sinks = [logging.StreamHandler(console), RotatingFileHandler(file),
                 _RingLogHandler(ring), _QueueLogHandler(queue)]
        for sink in sinks:
            log_sinks.attach(sink)
        source = tmp_path / "provider.py"
        source.write_text(
            "import logging\n"
            "def write():\n"
            "    logging.getLogger('brand_new_dynamic_logger').info('loaded-app-message')\n"
        )
        app = "logging-proof"
        try:
            module = load_bundle_module(tmp_path, app, "provider")
            await asyncio.to_thread(module.write)
            await asyncio.sleep(0)
            logging.getLogger("ordinary-library").info("library-info-hidden")
            assert "loaded-app-message" in console.getvalue()
            assert "loaded-app-message" in file.read_text()
            assert any("loaded-app-message" in line for line in ring)
            assert "loaded-app-message" in await asyncio.wait_for(queue.get(), 2)
            assert "library-info-hidden" not in console.getvalue()
            code_provenance.release(app)
            module.write()
            assert console.getvalue().count("loaded-app-message") == 1
        finally:
            code_provenance.release(app)
            for sink in sinks:
                log_sinks.detach(sink)
                sink.close()
            sys.modules.pop(module.__name__, None)
            log_sinks.set_level(old)

    asyncio.run(check())


def test_real_child_drains_both_streams_masked_with_bounded_tail(tmp_path):
    secret = "unguessable-proxy-credential-12345"
    old = log_sinks.level()
    log_sinks.set_level(logging.INFO)
    console = io.StringIO()
    handler = logging.StreamHandler(console)
    log_sinks.attach(handler)
    program = (
        "import sys\n"
        f"print('secret={secret}', flush=True)\n"
        "print('/root/private-location/config.py', file=sys.stderr, flush=True)\n"
        "print('x' * 20000, file=sys.stderr, flush=True)\n"
        "for i in range(500): print('stderr-line-' + str(i), file=sys.stderr)\n"
        "sys.exit(7)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = ChildOutput(app="child-proof", process="worker", pid=proc.pid,
                         env={"API_SECRET": secret})
    try:
        relay(proc, output)
        assert proc.wait(timeout=10) == 7
        assert output.finished.wait(5)
        report = output.report()
        assert report is not None and report["exitCode"] == 7
        assert len(report["lines"]) <= TAIL_LINES
        assert all(len(line) <= LINE_MAX_CHARS + 10 for line in report["lines"])
        assert secret not in console.getvalue()
        assert "/root/private-location" not in console.getvalue()
        assert "stdout" in console.getvalue() and "stderr" in console.getvalue()
        assert "output lines omitted" in console.getvalue()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        log_sinks.detach(handler)
        log_sinks.set_level(old)


def test_real_backend_worker_and_engine_output(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    app = "process-log-proof"
    root = home / "apps" / app
    root.mkdir(parents=True)
    metadata = {
        "name": app, "version": "1.0.0", "displayName": app,
        "description": "Process output verification",
        "backend": {"entryPoint": "backend.py", "type": "python"},
        "permissions": {"backgroundTasks": True},
    }
    (root / "app.json").write_text(json.dumps(metadata))
    (root / "installed.json").write_text(json.dumps({"name": app, "version": "1.0.0", "enabled": True}))
    (root / "backend.py").write_text(
        "import os,sys\n"
        "print('backend stdout', flush=True)\n"
        "print('backend stderr', file=sys.stderr, flush=True)\n"
        "sys.exit(5)\n"
    )
    (root / "worker.py").write_text(
        "import sys\nprint('worker stdout', flush=True)\n"
        "print('worker stderr', file=sys.stderr, flush=True)\nsys.exit(6)\n"
    )
    engine = root / "engine.py"
    engine.write_text(
        "import sys\n"
        "def load(**kwargs):\n"
        "    print('engine output', file=sys.stderr, flush=True)\n"
        "    return {'ready': True}\n"
    )
    manifest = AppManifest.from_json_file(root / "app.json")
    backend, workers = BackendSupervisor(), WorkerSupervisor()
    runner = SidecarRunner(app=app, worker=engine, python=sys.executable, restart_max=0)
    try:
        running = backend.start(manifest)
        assert running is not None and running.output is not None
        assert running.proc.wait(timeout=10) == 5
        assert running.output.finished.wait(5)
        assert any("backend stderr" in line for line in running.output.lines())
        assert backend.last_exit(app)["exitCode"] == 5
        records = workers.start(manifest)
        assert len(records) == 1 and records[0].output is not None
        assert records[0].proc.wait(timeout=10) == 6
        assert records[0].output.finished.wait(5)
        assert any("worker stdout" in line for line in records[0].output.lines())
        assert runner.call("load", {}) == {"ready": True}
        deadline = time.monotonic() + 5
        while not any("engine output" in line for line in runner.log_tail) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert any("engine output" in line for line in runner.log_tail)
    finally:
        backend.stop_all()
        workers.stop(app)
        runner.stop()

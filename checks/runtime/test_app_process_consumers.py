import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.apps import backend_runtime, worker_runtime
from gideon.extensions.apps.manifest import AppManifest
from gideon.interfaces.dashboard.handlers.apps import api_app_get
from gideon.operations.resilience.doctor import DoctorContext, _probe_apps


def test_real_exit_api_doctor_restart_and_owner_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    name = "consumer-proof"
    root = tmp_path / "apps" / name
    root.mkdir(parents=True)
    metadata = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": "Process status verification",
        "backend": {"entryPoint": "backend.py", "type": "python"},
        "permissions": {"backgroundTasks": True},
    }
    (root / "app.json").write_text(json.dumps(metadata))
    (root / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True})
    )
    script = root / "backend.py"
    script.write_text(
        "import sys\nprint('/root/private/failure.py failed', file=sys.stderr, flush=True)\nsys.exit(9)\n"
    )
    manifest = AppManifest.from_json_file(root / "app.json")
    sup = backend_runtime.BackendSupervisor()
    monkeypatch.setattr(backend_runtime, "_supervisor", sup)
    monkeypatch.setattr(
        worker_runtime, "_supervisor", worker_runtime.WorkerSupervisor()
    )

    async def check():
        server = web.Application()
        server.router.add_get("/api/apps/{name}", api_app_get)
        async with TestClient(TestServer(server)) as client:
            running = sup.start(manifest)
            assert running.proc.wait(timeout=10) == 9
            assert running.output.finished.wait(5)
            response = await client.get("/api/apps/" + name)
            assert response.status == 200
            data = await response.json()
            assert not data["backendRunning"] and data["backendExit"]["exitCode"] == 9
            assert "/root/private" not in json.dumps(data["backendExit"])
            probe = await _probe_apps(DoctorContext())
            assert (
                not probe.ok
                and probe.evidence["backends"][name]["exit"]["exitCode"] == 9
            )
            # A detail read has removed the dead table entry: owner stop must still clear its report.
            sup.stop(name)
            assert (await (await client.get("/api/apps/" + name)).json())[
                "backendExit"
            ] is None
            script.write_text("import time\ntime.sleep(60)\n")
            restarted = sup.start(manifest)
            assert restarted is not None
            data = await (await client.get("/api/apps/" + name)).json()
            assert data["backendRunning"] and data["backendExit"] is None
            assert (await _probe_apps(DoctorContext())).ok
            sup.stop(name)
            data = await (await client.get("/api/apps/" + name)).json()
            assert not data["backendRunning"] and data["backendExit"] is None

    try:
        asyncio.run(check())
    finally:
        sup.stop_all()

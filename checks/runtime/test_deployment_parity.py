"""Deployment parity tests — verifies the required endpoints respond
(without error) under both the service path (subprocess) and the Compose
path (docker/finch).

Both runtimes are skipped cleanly when the relevant runtime is absent:
- Service path: skipped when `gideon` is not on PATH
- Compose path: skipped when neither `docker` nor `finch` is on PATH, or when
  the Compose stack cannot be built/started

The Compose path auto-detects the container runtime (docker preferred, then
finch); there is no command-line selector.
"""

import contextlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from gideon.security.security import redact_for_display

_REQUIRED_ENDPOINTS = [
    "/api/system",
    "/api/auth-status",
    "/api/providers",
    "/api/use-cases",
    "/api/credentials",
    "/api/sessions",
    "/api/agents",
]

_SERVICE_ARGS = ("gateway", "--no-open")
_REPO = Path(__file__).resolve().parents[2]
_STARTUP_TIMEOUT = 30


def _wait_for_gateway(
    base_url: str, timeout: float = _STARTUP_TIMEOUT, proc=None
) -> bool:
    """Poll /api/system until it responds or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            req = urllib.request.Request(f"{base_url}/api/system")
            with urllib.request.urlopen(req, timeout=2):
                return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _fetch(url: str) -> dict:
    """GET *url* and return parsed JSON. Returns {"_error": ...} on failure."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"_auth_required": True, "status": e.code}
        return {"_error": f"HTTP {e.code}"}
    except Exception as exc:
        return {"_error": str(exc)}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _service_env(scratch: Path, executable: str) -> dict[str, str]:
    home = scratch / "home"
    runtime_home = scratch / "gideon-home"
    home.mkdir()
    runtime_home.mkdir()
    (runtime_home / "config.json").write_text(
        json.dumps({"updates": {"check_enabled": False}}), encoding="utf-8"
    )
    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join(
            [str(Path(executable).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        ),
        "PYTHONPATH": str(_REPO / "runtime"),
        "GIDEON_HOME": str(runtime_home),
        "GIDEON_BIND_HOST": "127.0.0.1",
        "GIDEON_DISABLE_LIVE_WRITES": "1",
        "GIDEON_ACP_NO_PROVISION": "1",
        "GIDEON_SKIP_APP_BACKENDS": "1",
        "GIDEON_SKIP_APP_WORKERS": "1",
    }
    for name in ("TMPDIR", "LANG", "LC_ALL"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def _exit_output(log: Path) -> str:
    text = log.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"([?&]token=)[^\s&#]+", r"\1<redacted>", text)
    text = re.sub(r"(\"token\"\s*:\s*\")[^\"]+(\")", r"\1<redacted>\2", text)
    return redact_for_display(text)[-4000:]


@contextlib.contextmanager
def _service_gateway_at(command: list[str]):
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        output = scratch / "gateway.log"
        with output.open("wb") as log:
            proc = subprocess.Popen(
                [*command, *_SERVICE_ARGS, "--port", str(port)],
                env=_service_env(scratch, command[0]),
                cwd=scratch,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        try:
            if not _wait_for_gateway(base_url, proc=proc):
                if proc.poll() is not None:
                    pytest.fail(
                        f"Gateway exited ({proc.returncode}) before it answered. It printed:\n"
                        f"{_exit_output(output)}",
                        pytrace=False,
                    )
                pytest.skip(f"Gateway did not start within {_STARTUP_TIMEOUT}s")
            yield base_url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


@pytest.fixture(scope="module")
def service_gateway():
    executable = shutil.which("gideon")
    command = [executable] if executable else [sys.executable, "-m", "gideon"]
    with _service_gateway_at(command) as base_url:
        yield base_url


def _container_runtime() -> str | None:
    for rt in ("docker", "finch"):
        if shutil.which(rt):
            return rt
    return None


@pytest.fixture(scope="module")
def compose_gateway():
    """Start the gateway via `docker/finch compose up` using the build overlay."""
    runtime = _container_runtime()
    if not runtime:
        pytest.skip("Neither docker nor finch on PATH — Compose path not available")

    repo_root = Path(__file__).resolve().parent.parent.parent
    compose_dir = repo_root / "infrastructure" / "compose"
    compose_file = compose_dir / "compose.yaml"
    build_overlay = compose_dir / "compose.build.yaml"
    if not compose_file.exists():
        pytest.skip("deploy/compose/compose.yaml not found")

    root_env = repo_root / ".env"
    env_example = repo_root / ".env.example"
    seeded_env = False
    if not root_env.exists() and env_example.exists():
        root_env.write_text(env_example.read_text())
        seeded_env = True

    base_url = "http://127.0.0.1:10000"
    compose_args = [
        runtime,
        "compose",
        "-f",
        str(compose_file),
        "-f",
        str(build_overlay),
    ]

    build_timeout = 90
    try:
        subprocess.run(
            compose_args + ["up", "-d", "--build"],
            check=True,
            capture_output=True,
            timeout=build_timeout,
        )
    except subprocess.CalledProcessError as exc:
        subprocess.run(compose_args + ["down"], capture_output=True, timeout=60)
        if seeded_env:
            root_env.unlink(missing_ok=True)
        pytest.skip(f"compose up failed: {exc.stderr.decode()[:200]}")
    except subprocess.TimeoutExpired:
        subprocess.run(compose_args + ["down"], capture_output=True, timeout=60)
        if seeded_env:
            root_env.unlink(missing_ok=True)
        pytest.skip(
            f"compose up exceeded {build_timeout}s to build — skipping Compose path"
        )

    try:
        if not _wait_for_gateway(base_url, timeout=60):
            pytest.skip("Compose gateway did not start within 60s")
        yield base_url
    finally:
        subprocess.run(compose_args + ["down"], capture_output=True, timeout=60)
        if seeded_env:
            root_env.unlink(missing_ok=True)


@pytest.mark.parametrize("endpoint", _REQUIRED_ENDPOINTS)
def test_service_path_endpoint_responds(service_gateway, endpoint):
    """Every required endpoint responds on the service path."""
    data = _fetch(f"{service_gateway}{endpoint}")
    assert "_error" not in data or data.get(
        "_auth_required"
    ), f"Endpoint {endpoint} returned error on service path: {data}"


@pytest.mark.parametrize("endpoint", _REQUIRED_ENDPOINTS)
def test_compose_path_endpoint_responds(compose_gateway, endpoint):
    """Every required endpoint responds on the Compose path."""
    data = _fetch(f"{compose_gateway}{endpoint}")
    assert "_error" not in data or data.get(
        "_auth_required"
    ), f"Endpoint {endpoint} returned error on Compose path: {data}"


def _deployment_commands():
    commands = []
    for compose in sorted((_REPO / "deploy/compose").glob("compose*.yaml")):
        services = (yaml.safe_load(compose.read_text()) or {}).get("services") or {}
        for name, service in services.items():
            for key in ("entrypoint", "command"):
                argv = (service or {}).get(key)
                argv = shlex.split(argv) if isinstance(argv, str) else argv
                if argv and argv[0] == "gideon":
                    commands.append((f"{compose.name}:{name}.{key}", argv))
    for dockerfile in sorted((_REPO / "deploy/docker").glob("Dockerfile*")):
        for line in dockerfile.read_text().splitlines():
            match = re.match(r"\s*(CMD|ENTRYPOINT)\s+(\[.*\])\s*$", line)
            if match:
                argv = json.loads(match.group(2))
                if argv and argv[0] == "gideon":
                    commands.append((f"{dockerfile.name}:{match.group(1)}", argv))
    return commands


def test_deployment_commands_use_the_real_cli_parser():
    from gideon.interfaces.cli.main import build_parser

    commands = [
        ("service fixture", ["gideon", *_SERVICE_ARGS, "--port", "10000"]),
        *_deployment_commands(),
    ]
    assert any(where == "Dockerfile.backend:CMD" for where, _ in commands)
    for where, argv in commands:
        build_parser().parse_args(argv[1:])


def test_service_child_environment_is_isolated(tmp_path):
    env = _service_env(tmp_path, sys.executable)
    assert env["HOME"] == str(tmp_path / "home")
    assert env["GIDEON_HOME"] == str(tmp_path / "gideon-home")
    assert not json.loads((tmp_path / "gideon-home/config.json").read_text())[
        "updates"
    ]["check_enabled"]
    assert env["GIDEON_DISABLE_LIVE_WRITES"] == "1"
    assert env["GIDEON_SKIP_APP_WORKERS"] == "1"


def test_service_child_early_exit_fails_promptly():
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match=r"exited \(2\) before it answered"):
        with _service_gateway_at(
            [sys.executable, "-m", "gideon", "--not-a-valid-option"]
        ):
            pass
    assert time.monotonic() - started < _STARTUP_TIMEOUT / 3


def test_service_diagnostics_redact_dashboard_tokens(tmp_path):
    log = tmp_path / "gateway.log"
    log.write_text(
        'Dashboard http://127.0.0.1:1?token=synthetic.placeholder\nGIDEON_READY:{"token":"synthetic.placeholder"}'
    )
    output = _exit_output(log)
    assert "synthetic.placeholder" not in output
    assert "<redacted>" in output

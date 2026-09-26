"""Exercise the built single-container image with a recreated state volume."""

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4

import pytest


def _docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_single_container_serves_console_and_preserves_workspace():
    image = os.environ.get("GIDEON_SINGLE_CONTAINER_IMAGE")
    if not image:
        pytest.skip("set GIDEON_SINGLE_CONTAINER_IMAGE to run the image smoke test")
    if not shutil.which("docker"):
        pytest.fail("Docker is required for the single-container image smoke test")

    name = f"gideon-single-smoke-{uuid4().hex[:12]}"
    volume = f"{name}-home"
    marker = "/data/workspace/container-smoke.txt"

    def start() -> None:
        _docker(
            "run", "-d", "--name", name, "-p", "127.0.0.1::10000",
            "-v", f"{volume}:/data", image,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                port = _docker("port", name, "10000/tcp").rsplit(":", 1)[-1]
            except subprocess.CalledProcessError:
                pytest.fail(f"gateway stopped before it was healthy:\n{_docker('logs', name)}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/healthz", timeout=2
                ) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(1)
        pytest.fail(f"gateway did not become healthy:\n{_docker('logs', name)}")

    try:
        _docker("volume", "create", volume)
        start()
        _docker(
            "exec", name, "python", "-c",
            "from pathlib import Path; "
            "from gideon.core.config.loader import workspace_root; "
            "from gideon.core.layout import package_path; "
            "assert workspace_root() == Path('/data/workspace'); "
            "assert package_path('static', 'dist', 'index.html').is_file()",
        )
        _docker("exec", name, "sh", "-c", f"printf persisted > {marker}")
        _docker("rm", "-f", name)

        start()
        assert _docker("exec", name, "cat", marker) == "persisted"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(
            ["docker", "volume", "rm", volume], capture_output=True, check=False
        )


def test_single_container_update_instructions(monkeypatch):
    from gideon.operations.self_update import container_instructions

    monkeypatch.setenv("GIDEON_DOCKER_MODE", "single")
    commands = container_instructions()
    assert "--target single" in commands[0]
    assert "docker stop gideon && docker rm gideon" == commands[1]
    assert "-v gideon_home:/data" in commands[2]

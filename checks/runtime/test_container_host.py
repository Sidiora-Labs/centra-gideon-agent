"""Documented container modes retain their image choice and original mounts."""

import shlex
from pathlib import Path

from gideon.operations import container_host, self_update


def assert_install_commands(monkeypatch):
    for key in (
        container_host.STARTED_BY_ENV,
        "GIDEON_CONTAINER_NAME",
        "GIDEON_CONTAINER_IMAGE",
        "GIDEON_GATEWAY_IMAGE",
        "GIDEON_WEB_IMAGE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIDEON_DOCKER_MODE", "single")
    run = container_host.run_command()
    argv = shlex.split(run)
    assert argv[argv.index("--name") + 1] == "gideon"
    assert argv[argv.index("-v") + 1] == "gideon_home:/data"
    assert argv[argv.index("-p") + 1] == "127.0.0.1:10000:10000"
    assert argv[-1] == "gideon:local"
    guide = Path("docs/guides/CONTAINERS.md").read_text()
    assert run in guide
    for tag in ("", "0.2"):
        commands = self_update.container_instructions(tag)
        assert "--target single -t gideon:local" in commands[1]
        assert (
            "docker stop gideon && docker rename gideon gideon-previous" in commands[2]
        )
        assert "--volumes-from gideon-previous gideon:local" in commands[2]
        assert "--env-file <(docker inspect" in commands[2]
        assert "docker rm" not in "\n".join(commands)
    assert container_host.stop_command() == "docker stop gideon"
    assert container_host.restart_command() == "docker restart gideon"
    monkeypatch.setenv("GIDEON_CONTAINER_NAME", "custom-gideon")
    monkeypatch.setenv(
        "GIDEON_CONTAINER_IMAGE", "registry.example:5000/team/gideon:1.0"
    )
    commands = container_host.update_commands("0.2")
    assert commands[1] == "docker pull registry.example:5000/team/gideon:0.2"
    assert (
        "--volumes-from custom-gideon-previous registry.example:5000/team/gideon:0.2"
        in commands[2]
    )
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")
    compose = "docker compose -f infrastructure/compose/compose.yaml"
    assert container_host.stop_command() == compose + " stop gideon-gateway"
    assert container_host.restart_command() == compose + " restart gideon-gateway"
    assert container_host.update_commands("0.2")[1] == (
        compose + " -f infrastructure/compose/compose.build.yaml up -d --build"
    )
    monkeypatch.setenv("GIDEON_GATEWAY_IMAGE", "registry.example/gideon-api:1.0")
    monkeypatch.setenv("GIDEON_WEB_IMAGE", "registry.example/gideon-web:1.0")
    commands = container_host.update_commands("0.2")
    for command, action in zip(commands[1:], ("pull", "up -d")):
        assert command == (
            "GIDEON_GATEWAY_IMAGE=registry.example/gideon-api:0.2 "
            "GIDEON_WEB_IMAGE=registry.example/gideon-web:0.2 " + compose + " " + action
        )
    assert "down" not in "\n".join(commands)
    compose_source = Path("infrastructure/compose/compose.yaml").read_text()
    assert "GIDEON_CONTAINER_STARTED_BY: compose" in compose_source
    assert "gideon_home:/data:z" in compose_source
    dockerfile = Path("infrastructure/docker/Dockerfile.backend").read_text()
    assert "GIDEON_CONTAINER_STARTED_BY=docker-run" in dockerfile
    assert "GIDEON_CONTAINER_IMAGE=gideon:local" in dockerfile

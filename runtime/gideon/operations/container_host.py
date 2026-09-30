"""Host lifecycle guidance for self-hosted container installations."""

from __future__ import annotations

import os
import shlex

STARTED_BY_ENV = "GIDEON_CONTAINER_STARTED_BY"
COMPOSE_SERVICE = "gideon-gateway"
COMPOSE_FILE = "infrastructure/compose/compose.yaml"


def in_container() -> bool:
    from gideon.operations.self_update import detect_install_kind

    return detect_install_kind() == "container"


def started_by_compose() -> bool:
    return os.environ.get(STARTED_BY_ENV, "").strip().lower() == "compose"


def _name() -> str:
    return os.environ.get("GIDEON_CONTAINER_NAME", "").strip() or "gideon"


def _compose() -> str:
    return f"docker compose -f {shlex.quote(COMPOSE_FILE)}"


def stop_command() -> str:
    if started_by_compose():
        return f"{_compose()} stop {COMPOSE_SERVICE}"
    return f"docker stop {shlex.quote(_name())}"


def restart_command() -> str:
    if started_by_compose():
        return f"{_compose()} restart {COMPOSE_SERVICE}"
    return f"docker restart {shlex.quote(_name())}"


def not_done_here(done: str, host_command: str) -> str:
    return (
        "Gideon is running in a container; the container runtime starts and stops it. "
        f"Nothing was {done}. On the host, run:\n\n    {host_command}"
    )


def run_command(image: str = "gideon:local", *, volumes_from: str = "") -> str:
    mount = (
        f"--volumes-from {shlex.quote(volumes_from)}"
        if volumes_from
        else "-v gideon_home:/data"
    )
    environment = (
        "--env-file <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "
        f"{shlex.quote(volumes_from)}) -e GIDEON_CONTAINER_IMAGE={shlex.quote(image)} "
        if volumes_from else ""
    )
    return (
        f"docker run -d --name {shlex.quote(_name())} --restart unless-stopped "
        f"-p 127.0.0.1:10000:10000 {environment}{mount} {shlex.quote(image)}"
    )


def _tagged(image: str, tag: str) -> str:
    if not tag or image.endswith(":local"):
        return image
    image = image.split("@", 1)[0]
    if ":" in image.rsplit("/", 1)[-1]:
        image = image.rsplit(":", 1)[0]
    return f"{image}:{tag}"


def update_commands(tag: str = "") -> list[str]:
    if started_by_compose():
        image = os.environ.get("GIDEON_GATEWAY_IMAGE", "gideon-gateway:local")
        web = os.environ.get("GIDEON_WEB_IMAGE", "gideon-web:local")
        if image.endswith(":local") and web.endswith(":local"):
            return [
                "# From the original checkout, keep the same Compose project, files, .env and volume overrides.",
                f"{_compose()} -f infrastructure/compose/compose.build.yaml up -d --build",
            ]
        prefix = (
            f"GIDEON_GATEWAY_IMAGE={shlex.quote(_tagged(image, tag))} "
            f"GIDEON_WEB_IMAGE={shlex.quote(_tagged(web, tag))} "
        )
        return [
            "# From the original checkout, set GIDEON_GATEWAY_IMAGE and GIDEON_WEB_IMAGE to the desired full image references in .env; retain all original Compose files and project options.",
            f"{prefix}{_compose()} pull",
            f"{prefix}{_compose()} up -d",
        ]
    single = os.environ.get("GIDEON_DOCKER_MODE") == "single"
    image = os.environ.get("GIDEON_CONTAINER_IMAGE", "").strip() or (
        "gideon:local" if single else "gideon-gateway:local"
    )
    image = _tagged(image, tag)
    name = shlex.quote(_name())
    previous = _name() + "-previous"
    prepare = (
        f"docker build -f infrastructure/docker/Dockerfile.backend --target {'single' if single else 'runtime'} -t {shlex.quote(image)} ."
        if image.endswith(":local")
        else f"docker pull {shlex.quote(image)}"
    )
    return [
        "# In bash, use the actual original container name and image (GIDEON_CONTAINER_NAME/GIDEON_CONTAINER_IMAGE), and repeat its original port and other run options in the replacement command.",
        prepare,
        f"docker stop {name} && docker rename {name} {shlex.quote(previous)} && {run_command(image, volumes_from=previous)}",
        f"# The retained {shlex.quote(previous)} container supplies every original data/workspace mount. Keep it until the replacement is healthy; do not remove its volumes.",
    ]

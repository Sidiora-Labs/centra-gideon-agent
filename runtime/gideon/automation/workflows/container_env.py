"""Container manifest admission and CLI lifecycle plans."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.workflows.workspace import SpecIssue

logger = logging.getLogger(__name__)
WORKSPACE_MOUNT = "/workspace"
_KEEPALIVE = ("sleep", "infinity")
_VERB_TIMEOUT_SECS = 60
_PROVISION_TIMEOUT_SECS = 900


@dataclass
class EnvironmentManifest:
    """§4.4's typed environment manifest.

    `image` XOR `build` is the load-bearing rule: both is ambiguous about which wins,
    neither provisions nothing, and both cases are authoring mistakes better named at
    save time than discovered as a subprocess error.
    """

    image: str = ""
    build: dict[str, str] = field(default_factory=dict)
    user: str = ""
    mounts: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)

    @property
    def declared(self) -> bool:
        return bool(self.image or self.build)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "build": dict(self.build),
            "user": self.user,
            "mounts": [dict(m) for m in self.mounts],
            "capabilities": list(self.capabilities),
        }


@dataclass
class BackendResult:
    """One lifecycle verb's outcome. `ok=False` carries the reason; nothing here raises."""

    ok: bool
    value: str = ""
    reason: str = ""


class _ManifestReader:
    def __init__(self, source):
        self.source = source
        self.issues = []
        self.manifest = EnvironmentManifest()

    def issue(self, code, message, fatal=True):
        self.issues.append(SpecIssue(code, message, fatal=fatal))

    def environment(self):
        document, result = self.source, self.manifest
        result.image = str(document.get("image", "") or "").strip()
        build = document.get("build")
        if isinstance(build, dict):
            result.build = {
                "dockerfile": str(build.get("dockerfile", "") or "").strip(),
                "context": str(build.get("context", "") or ".").strip() or ".",
            }
            if not result.build["dockerfile"]:
                self.issue(
                    "container_build_no_dockerfile",
                    "container.build needs a `dockerfile` path",
                )
        elif build is not None:
            self.issue(
                "container_build_not_object",
                "container.build must be an object with dockerfile/context",
            )
        present = bool(result.image), bool(result.build)
        if all(present):
            self.issue(
                "container_image_xor_build",
                "declare container.image OR container.build, not both — with both declared it is ambiguous which environment the run actually gets",
            )
        elif not any(present):
            self.issue(
                "container_no_environment",
                "container mode needs an environment: declare container.image or container.build",
            )

    def mounts(self):
        for mount in self.source.get("mounts") or []:
            if not isinstance(mount, dict):
                self.issue(
                    "container_mount_not_object",
                    f"mount entry {mount!r} must be an object with source/target",
                    fatal=False,
                )
                continue
            source, target = (
                str(mount.get(key, "") or "").strip() for key in ("source", "target")
            )
            if not source or not target:
                self.issue(
                    "container_mount_incomplete",
                    "a mount needs both `source` and `target`",
                )
            elif (target.rstrip("/") or "/") in (WORKSPACE_MOUNT, "/"):
                self.issue(
                    "container_mount_reserved",
                    f"mount target {target!r} is engine-owned — the run workspace is always mounted at {WORKSPACE_MOUNT}, and shadowing it would make every stage write into a directory nobody reads back",
                )
            else:
                self.manifest.mounts.append(
                    dict(
                        source=source,
                        target=target,
                        readonly=bool(mount.get("readonly")),
                    )
                )

    def capabilities(self):
        for value in self.source.get("capabilities") or []:
            capability = str(value or "").strip()
            if not capability:
                continue
            if capability.lower() in ("privileged", "all"):
                self.issue(
                    "container_privileged_refused",
                    f"capability {capability!r} is refused — a privileged container is the isolation switched off, not a capability grant",
                )
            else:
                self.manifest.capabilities.append(capability)

    def read(self):
        for phase in (self.environment, self.mounts, self.capabilities):
            phase()
        self.manifest.user = str(self.source.get("user", "") or "").strip()
        return self.manifest, self.issues


def parse_manifest(raw: Any) -> tuple[EnvironmentManifest, list[SpecIssue]]:
    if raw is None:
        return EnvironmentManifest(), []
    if isinstance(raw, dict):
        return _ManifestReader(raw).read()
    return EnvironmentManifest(), [
        SpecIssue(
            "container_not_object", "workspace.container must be an object", fatal=True
        )
    ]


async def _run_cli(
    argv: list[str], *, timeout: float = _VERB_TIMEOUT_SECS, cwd: str = ""
) -> BackendResult:
    try:
        from gideon.security.sandbox import create_subprocess_limited

        process = await create_subprocess_limited(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except FileNotFoundError:
        failure = f"{argv[0]} is not installed"
    except asyncio.TimeoutError:
        failure = f"{argv[0]} timed out after {timeout:.0f}s"
    except Exception as error:
        failure = str(error)
    else:
        if process.returncode == 0:
            return BackendResult(
                True, value=(stdout or b"").decode(errors="replace").strip()
            )
        detail = (stderr or stdout or b"").decode(errors="replace").strip()
        return BackendResult(
            False, reason=detail[-500:] or f"exit {process.returncode}"
        )
    return BackendResult(False, reason=failure)


class _ContainerLaunch:
    def __init__(self, binary, manifest, workspace, run_id, snapshot, context):
        self.binary, self.manifest = binary, manifest
        self.workspace, self.run_id = workspace, run_id
        self.snapshot, self.context = snapshot, context
        self.name = f"gideon-run-{run_id}"

    async def execute(self):
        image = self.snapshot
        if not image and self.manifest.build:
            image = f"gideon/run-{self.run_id}:build"
            recipe = self.manifest.build
            command = [
                self.binary,
                "build",
                "-t",
                image,
                "-f",
                recipe.get("dockerfile", ""),
                recipe.get("context", ".") or ".",
            ]
            built = await _run_cli(
                command, timeout=_PROVISION_TIMEOUT_SECS, cwd=self.context
            )
            if not built.ok:
                return built
        image = image or self.manifest.image
        if not image:
            return BackendResult(
                False, reason="manifest declares no image and no build"
            )
        result = await _run_cli(self.arguments(image), timeout=_PROVISION_TIMEOUT_SECS)
        return BackendResult(True, value=self.name) if result.ok else result

    def arguments(self, image):
        options = [
            ("--name", self.name),
            ("--entrypoint", _KEEPALIVE[0]),
            ("--volume", f"{self.workspace}:{WORKSPACE_MOUNT}"),
            ("--workdir", WORKSPACE_MOUNT),
        ]
        if self.manifest.user:
            options.append(("--user", self.manifest.user))
        for mount in self.manifest.mounts:
            suffix = ":ro" if mount.get("readonly") else ""
            options.append(("--volume", f"{mount['source']}:{mount['target']}{suffix}"))
        options.extend(("--cap-add", value) for value in self.manifest.capabilities)
        return [
            self.binary,
            "run",
            "--detach",
            *(part for option in options for part in option),
            image,
            *_KEEPALIVE[1:],
        ]


class CliContainerBackend:
    can_snapshot = True

    def __init__(self, binary: str) -> None:
        self.binary = binary

    @property
    def name(self) -> str:
        return self.binary

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    async def provision(
        self,
        manifest: EnvironmentManifest,
        *,
        workspace_dir: str,
        run_id: str,
        from_snapshot: str = "",
        context_dir: str = "",
    ) -> BackendResult:
        return await _ContainerLaunch(
            self.binary, manifest, workspace_dir, run_id, from_snapshot, context_dir
        ).execute()

    async def snapshot(self, container_id: str, *, tag: str) -> BackendResult:
        outcome = await _run_cli(
            [self.binary, "commit", container_id, tag], timeout=_PROVISION_TIMEOUT_SECS
        )
        if not outcome.ok:
            return outcome
        return BackendResult(True, value=tag)

    async def remove(self, container_id: str) -> BackendResult:
        return await _run_cli([self.binary, "rm", "--force", container_id])


class AppleContainerBackend(CliContainerBackend):
    can_snapshot = False

    def __init__(self) -> None:
        super().__init__("container")

    async def snapshot(self, container_id: str, *, tag: str) -> BackendResult:
        return BackendResult(
            False,
            reason="Apple's container CLI has no commit verb — checkpoints on this backend carry no workspace snapshot, and fork provisions the child fresh",
        )


_BACKENDS = (
    lambda: CliContainerBackend("docker"),
    lambda: CliContainerBackend("nerdctl"),
    lambda: AppleContainerBackend(),
)


def detect_backend() -> CliContainerBackend | None:
    candidates = (factory() for factory in _BACKENDS)
    return next((candidate for candidate in candidates if candidate.available()), None)

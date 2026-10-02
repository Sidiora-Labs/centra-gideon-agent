from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

try:
    from .source_closure import (
        SourceClosureError,
        materialize_frozen_source,
        validate_frozen_source,
    )
except ImportError:
    from source_closure import (
        SourceClosureError,
        materialize_frozen_source,
        validate_frozen_source,
    )


REPOSITORY = Path(__file__).resolve().parents[3]


class QualificationFailure(RuntimeError):
    pass


def _environment_flag(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in {"", "0", "false", "no"}:
        return False
    if value in {"1", "true", "yes"}:
        return True
    raise QualificationFailure(f"{name} must be a boolean flag")


def _docker_object_exists(kind: str, name: str) -> bool:
    result = subprocess.run(
        ["docker", kind, "inspect", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise QualificationFailure(
        f"docker {kind} inspection failed with exit {result.returncode}: "
        + result.stderr.strip()
    )


def _run(
    command: list[str],
    *,
    log: Path,
    cwd: Path = REPOSITORY,
    environment: dict[str, str] | None = None,
    timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    with log.open("a", encoding="utf-8") as stream:
        stream.write("COMMAND " + json.dumps(command) + "\n")
        stream.flush()
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        stream.write(f"EXIT {result.returncode}\n")
    if result.returncode != 0:
        raise QualificationFailure(
            f"command failed with exit {result.returncode}; see {log}"
        )
    return result


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _wheel_contents(wheel: Path) -> dict[str, object]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        daemon = next(
            (name for name in names if name.endswith("gideon/hypermid/bin/hypermid-daemon")),
            None,
        )
        if daemon is None:
            raise QualificationFailure("wheel omits the Hypermid daemon")
        required = {
            "gideon/hypermid/__init__.py",
            "gideon/hypermid/schemas/config.schema.json",
            "gideon/hypermid/schemas/common.schema.json",
            "gideon/static/dist/index.html",
        }
        missing = sorted(required - names)
        if missing:
            raise QualificationFailure("wheel is missing " + ", ".join(missing))
        daemon_bytes = archive.read(daemon)
        if not daemon_bytes.startswith(b"\x7fELF"):
            raise QualificationFailure("wheel daemon is not a real Linux executable")
        executable_mode = (archive.getinfo(daemon).external_attr >> 16) & 0o777
        if executable_mode & 0o111 == 0:
            raise QualificationFailure("wheel daemon is not executable")
        return {
            "entry_count": len(names),
            "daemon_path": daemon,
            "platform_wheel": not wheel.name.endswith("-any.whl"),
        }


def qualify(
    log: Path,
    *,
    source_root: Path,
    artifact_dir: Path,
    source_digest: str,
) -> dict[str, object]:
    missing_tools = [
        name for name in ("cargo", "docker", "npm") if shutil.which(name) is None
    ]
    if missing_tools:
        raise QualificationFailure(
            "clean-install qualification requires " + ", ".join(missing_tools)
        )
    source_root = source_root.resolve()
    artifact_dir = artifact_dir.resolve()
    if artifact_dir == source_root or source_root in artifact_dir.parents:
        raise QualificationFailure("packaging artifacts must be outside the frozen source")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    reserved = {
        artifact_dir / "source-closure.json",
        artifact_dir / "wheel-manifest.json",
        artifact_dir / "image-manifest.json",
        *artifact_dir.glob("*.whl"),
    }
    existing = sorted(path.name for path in reserved if path.exists())
    if existing:
        raise QualificationFailure(
            "packaging artifact destination is not fresh: " + ", ".join(existing)
        )
    try:
        closure = validate_frozen_source(source_root, source_digest)
    except SourceClosureError as error:
        raise QualificationFailure(str(error)) from error
    _write_json(artifact_dir / "source-closure.json", closure)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("", encoding="utf-8")
    run_id = uuid.uuid4().hex[:12]
    image = f"hypermid-packaged-install:{run_id}"
    volume = f"hypermid_packaged_install_{run_id}"
    container_prefix = f"hypermid-packaged-{run_id}"
    preserve_package_artifacts = _environment_flag(
        "GIDEON_PRESERVE_PACKAGE_ARTIFACTS"
    )
    if _docker_object_exists("image", image):
        raise QualificationFailure(f"packaging image already exists: {image}")
    if _docker_object_exists("volume", volume):
        raise QualificationFailure(f"packaging volume already exists: {volume}")
    with tempfile.TemporaryDirectory(prefix="hypermid-package-") as temporary:
        work = Path(temporary)
        wheel_source = work / "wheel-source"
        container_source = work / "container-source"
        try:
            materialize_frozen_source(source_root, wheel_source, closure)
            materialize_frozen_source(source_root, container_source, closure)
        except SourceClosureError as error:
            raise QualificationFailure(str(error)) from error
        wheel_dir = work / "wheel"
        wheel_dir.mkdir()
        build_environment = dict(os.environ)
        build_environment.update(
            {
                "CARGO_BUILD_JOBS": "2",
                "CARGO_TARGET_DIR": os.environ.get(
                    "CARGO_TARGET_DIR",
                    os.fspath(work / "cargo-target"),
                ),
                "PATH": f"/root/.cargo/bin:{os.environ.get('PATH', '')}",
            }
        )
        _run(
            [
                "npm",
                "ci",
                "--workspace=apps/console",
                "--prefer-offline",
                "--no-audit",
                "--no-fund",
            ],
            log=log,
            cwd=wheel_source,
            environment=build_environment,
        )
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                os.fspath(wheel_dir),
            ],
            log=log,
            cwd=wheel_source,
            environment=build_environment,
        )
        wheels = list(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise QualificationFailure(f"expected one wheel, found {len(wheels)}")
        wheel = wheels[0]
        wheel_result = _wheel_contents(wheel)
        preserved_wheel = artifact_dir / wheel.name
        if preserved_wheel.exists():
            raise QualificationFailure(f"wheel artifact already exists: {preserved_wheel}")
        shutil.copy2(wheel, preserved_wheel)
        wheel_manifest = {
            "schema_version": 1,
            "source_digest": source_digest,
            "artifact": wheel.name,
            "sha256": hashlib.sha256(preserved_wheel.read_bytes()).hexdigest(),
            "size_bytes": preserved_wheel.stat().st_size,
            "contents": wheel_result,
        }
        _write_json(artifact_dir / "wheel-manifest.json", wheel_manifest)

        venv = work / "venv"
        _run([sys.executable, "-m", "venv", os.fspath(venv)], log=log)
        _run(
            [
                os.fspath(venv / "bin" / "python"),
                "-m",
                "pip",
                "install",
                "--no-deps",
                os.fspath(wheel),
            ],
            log=log,
        )
        _run(
            [
                os.fspath(venv / "bin" / "python"),
                "-c",
                "import importlib.metadata as m; assert m.version('gideon-agent-harness')",
            ],
            log=log,
        )

        image_created = False
        volume_created = False
        try:
            _run(
                [
                    "docker",
                    "build",
                    "--memory",
                    "4g",
                    "--build-arg",
                    "CARGO_BUILD_JOBS=2",
                    "--target",
                    "single",
                    "-f",
                    "infrastructure/docker/Dockerfile.backend",
                    "-t",
                    image,
                    ".",
                ],
                log=log,
                cwd=container_source,
                environment=build_environment,
                timeout=3600,
            )
            image_created = True
            inspect = subprocess.run(
                ["docker", "image", "inspect", image],
                check=True,
                capture_output=True,
                text=True,
            )
            image_inspection = json.loads(inspect.stdout)
            if not isinstance(image_inspection, list) or len(image_inspection) != 1:
                raise QualificationFailure("container inspection returned an invalid result")
            image_record = image_inspection[0]
            image_user = str(image_record.get("Config", {}).get("User", ""))
            if image_user not in {"gideon", "10001", "10001:10001"}:
                raise QualificationFailure(
                    f"container user is not unprivileged: {image_user!r}"
                )
            _write_json(
                artifact_dir / "image-manifest.json",
                {
                    "schema_version": 1,
                    "source_digest": source_digest,
                    "image_tag": image,
                    "image_id": image_record.get("Id"),
                    "repo_digests": sorted(image_record.get("RepoDigests") or []),
                    "config_user": image_user,
                },
            )

            _run(["docker", "volume", "create", volume], log=log)
            volume_created = True
            probe = source_root / "checks/hypermid/packaging/container_probe.py"
            if not probe.is_file():
                raise QualificationFailure("frozen source omits the container probe")
            probe_mount = f"{probe}:/opt/hypermid-package-probe.py:ro"
            common = [
                "docker",
                "run",
                "--rm",
                "--memory",
                "2g",
                "--cpus",
                "2",
                "--user",
                "10001:10001",
                "-e",
                "GIDEON_HOME=/data",
                "-e",
                "GIDEON_WORKSPACE=/data/workspace",
                "-v",
                f"{volume}:/data",
                "-v",
                probe_mount,
                "--entrypoint",
                "/opt/venv/bin/python",
            ]
            _run(
                common
                + ["--name", f"{container_prefix}-first", image, "/opt/hypermid-package-probe.py", "first"],
                log=log,
            )
            _run(
                common
                + ["--name", f"{container_prefix}-second", image, "/opt/hypermid-package-probe.py", "second"],
                log=log,
            )
        finally:
            subprocess.run(
                ["docker", "rm", "-f", f"{container_prefix}-first", f"{container_prefix}-second"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if not preserve_package_artifacts:
                if volume_created:
                    subprocess.run(
                        ["docker", "volume", "rm", "-f", volume],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                if image_created:
                    subprocess.run(
                        ["docker", "image", "rm", "-f", image],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )

    return {
        "wheel": wheel_result,
        "container_user": image_user,
        "daemon_health": "passed",
        "python_adapter": "passed",
        "cli_status": "passed",
        "console": "present",
        "configured_root_persistence": "passed",
        "default_mode": "off",
        "transport": "authenticated_unix",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-digest", required=True)
    args = parser.parse_args()
    result = qualify(
        args.log.resolve(),
        source_root=args.source_root,
        artifact_dir=args.artifact_dir,
        source_digest=args.source_digest,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

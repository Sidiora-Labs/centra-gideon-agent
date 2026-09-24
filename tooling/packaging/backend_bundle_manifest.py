from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def runtime_files(repository: Path) -> list[Path]:
    root = repository / "runtime" / "gideon"
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]


def data_files(repository: Path) -> list[tuple[str, str]]:
    repository = repository.resolve()
    console = repository / "apps/console/dist"
    if not (console / "index.html").is_file():
        raise SystemExit(
            "Build the console before freezing the backend: make web-build"
        )
    root = repository / "runtime"
    return [
        (str(path), path.parent.relative_to(root).as_posix())
        for path in runtime_files(repository)
    ] + [(str(console), "gideon/static/dist")]


def manifest(repository: Path, *, include_console: bool = True) -> dict:
    repository = repository.resolve()
    root = repository / "runtime"
    inventory = [
        (path, path.relative_to(root).as_posix()) for path in runtime_files(repository)
    ]
    if include_console:
        data_files(repository)
        console = repository / "apps/console/dist"
        inventory.extend(
            (path, "gideon/static/dist/" + path.relative_to(console).as_posix())
            for path in sorted(console.rglob("*"))
            if path.is_file()
        )
    if not inventory:
        raise ValueError("runtime bundle inventory is empty")
    sdk = sorted(
        "gideon.sdk." + path.stem
        for path in (root / "gideon/sdk").glob("*.py")
        if path.name != "__init__.py"
    )
    return {
        "version": 1,
        "console": include_console,
        "sdk_modules": sdk,
        "files": [
            {"path": target, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path, target in inventory
        ],
    }


def write_manifest(repository: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest(repository), indent=2) + "\n", encoding="utf-8"
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    write_manifest(arguments.repository, arguments.output)

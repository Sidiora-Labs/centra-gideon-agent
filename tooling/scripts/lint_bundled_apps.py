"""Type-check each native app in isolation: bundle-relative module names may repeat."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLED = ROOT / "runtime/gideon/extensions/apps/native"
MIN_BUNDLES = 2


def bundle_sources(root: Path = BUNDLED) -> list[list[Path]]:
    return [
        files
        for bundle in sorted(root.iterdir())
        if bundle.is_dir()
        if (
            files := [
                p for p in sorted(bundle.rglob("*.py")) if "__pycache__" not in p.parts
            ]
        )
    ]


def main() -> int:
    bundles = bundle_sources()
    if len(bundles) < MIN_BUNDLES:
        raise RuntimeError(
            f"Expected at least {MIN_BUNDLES} Python bundles; found {len(bundles)}"
        )
    failed = False
    for files in bundles:
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--follow-imports=silent", *map(str, files)],
            cwd=ROOT,
            check=False,
        )
        failed |= result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

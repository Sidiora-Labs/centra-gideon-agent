"""Preserve old SOP directories outside the executable definition catalog."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)
_ARCHIVE_DIRNAME = "_legacy_sops"
_SOP_FILENAME = "WORKFLOW.md"


def _archive_destination(archive: Path, name: str) -> Path | None:
    filenames = [name, *(f"{name}-{number}" for number in range(2, 100))]
    return next(
        (
            candidate
            for filename in filenames
            if not (candidate := archive / filename).exists()
        ),
        None,
    )


def archive_legacy_sops(workflows_dir: Path) -> list[str]:
    if not workflows_dir.is_dir():
        return []
    archive = workflows_dir / _ARCHIVE_DIRNAME
    moved = []
    candidates = (
        entry
        for entry in sorted(workflows_dir.iterdir())
        if entry.is_dir()
        and not entry.name.startswith("_")
        and (entry / _SOP_FILENAME).is_file()
    )
    for entry in candidates:
        try:
            archive.mkdir(parents=True, exist_ok=True)
            destination = _archive_destination(archive, entry.name)
            if destination is None:
                logger.warning(
                    "legacy SOP %s: too many archived copies, skipped", entry.name
                )
                continue
            shutil.move(str(entry), str(destination))
        except OSError:
            logger.warning("Could not archive legacy SOP %s", entry.name, exc_info=True)
        else:
            moved.append(entry.name)
    if moved:
        logger.info(
            "Archived %d pre-v2 workflow SOP(s) to %s/: %s",
            len(moved),
            _ARCHIVE_DIRNAME,
            ", ".join(moved),
        )
    return moved

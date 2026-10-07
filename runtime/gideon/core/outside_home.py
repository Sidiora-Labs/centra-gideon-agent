"""Named, owner-approved read locations outside Gideon's active home."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Place:
    id: str
    label: str
    path: Path


def places() -> tuple[Place, ...]:
    return (
        Place(
            "agent-skills", "Shared agent skills", Path.home() / ".agents" / "skills"
        ),
    )


def allowed_ids() -> set[str]:
    try:
        from gideon.core.config.loader import AppConfig

        return set(AppConfig.load().security.outside_home)
    except Exception:
        return set()


def allowed_paths() -> list[tuple[str, str]]:
    permitted = allowed_ids()
    return [
        (place.label, str(place.path.resolve()))
        for place in places()
        if place.id in permitted and place.path.is_dir()
    ]


def place_rows() -> list[dict[str, object]]:
    permitted = allowed_ids()
    return [
        {
            "id": place.id,
            "label": place.label,
            "path": str(place.path),
            "allowed": place.id in permitted,
        }
        for place in places()
    ]

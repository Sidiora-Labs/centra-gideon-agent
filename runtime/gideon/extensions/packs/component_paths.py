"""Canonical destinations for installed pack components."""

from __future__ import annotations

from pathlib import Path


def _single_segment(value: str) -> bool:
    return (
        bool(value)
        and value not in {".", ".."}
        and len(value) <= 128
        and Path(value).name == value
        and not any(
            character in value for character in ("/", "\\", ":", "\x00", "\r", "\n")
        )
    )


def component_path(
    kind: str, component_id: str, home: Path, stage: str = ""
) -> Path | None:
    """Return the one home-relative destination used by pack install and removal.

    A skill destination is its directory because install locks and removal operate on
    the complete skill tree. Trigger destinations require their owning pack stage.
    Invalid path segments and unknown component kinds fail closed.
    """
    if not _single_segment(component_id):
        return None
    root = Path(home)
    if kind == "skill":
        return root / "skills" / component_id
    if kind == "template":
        return root / "workflows" / "defs" / component_id / "workflow.json"
    if kind == "prompt":
        return root / "prompts" / f"{component_id}.yaml"
    if kind == "agent":
        return root / "agents" / component_id / "agent.json"
    if kind == "trigger" and _single_segment(stage):
        return root / "packs" / "staged" / stage / "triggers" / f"{component_id}.json"
    return None

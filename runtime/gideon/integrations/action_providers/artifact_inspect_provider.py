"""Read workflow-owned output artifacts through character windows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import ActionClock


@dataclass(frozen=True)
class _TextWindow:
    offset: int
    length: int

    @classmethod
    def parse(cls, config: dict[str, Any], total: int) -> _TextWindow:
        try:
            start = int(config.get("offset", 0) or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("artifact_inspect 'offset' must be an integer") from error
        if start < 0:
            raise ValueError("artifact_inspect 'offset' must be >= 0")
        start = min(start, total)
        requested = config.get("length")
        if requested is None:
            return cls(start, total - start)
        try:
            count = int(requested)
        except (TypeError, ValueError) as error:
            raise ValueError("artifact_inspect 'length' must be an integer") from error
        if count <= 0:
            raise ValueError("artifact_inspect 'length' must be positive")
        return cls(start, count)

    def extract(self, reference: str, text: str) -> dict[str, Any]:
        stop = self.offset + self.length
        selected = text[self.offset : stop]
        return dict(
            ref=reference,
            content=selected,
            offset=self.offset,
            length=len(selected),
            total=len(text),
            truncated=stop < len(text),
        )


def _slice_bounds(cfg: dict[str, Any], total: int) -> tuple[int, int, str]:
    try:
        window = _TextWindow.parse(cfg, total)
    except ValueError as error:
        return 0, 0, str(error)
    return window.offset, window.length, ""


@dataclass(frozen=True)
class _ArtifactRead:
    run_id: str
    reference: str

    def load(self) -> Any:
        from gideon.automation.workflows import store

        return store.read_artifact(self.run_id, self.reference)


class ArtifactInspectActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "artifact_inspect"

    @property
    def display_name(self) -> str:
        return "Inspect Artifact"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        clock = ActionClock()
        reference = str(action_config.get("ref", "") or "").strip()
        if not reference:
            return ActionResult(
                False,
                error="artifact_inspect is missing 'ref' — bind it to {{nodes.x.artifact}}",
            )
        payload = getattr(ctx, "payload", None) or {}
        run_id = str(payload.get("run_id", "") or "")
        if not run_id:
            return ActionResult(
                False,
                error="artifact_inspect has no run context — it runs only inside a workflow run",
            )
        request = _ArtifactRead(run_id, reference)
        content = request.load()
        if content is None:
            return clock.result(
                False,
                error=f"artifact_inspect could not read {reference!r} — it is not a readable artifact of this run (a ref outside runs/<id>/artifacts/ is refused)",
            )
        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False)
        )
        try:
            window = _TextWindow.parse(action_config, len(text))
        except ValueError as error:
            return ActionResult(False, error=str(error))
        result = window.extract(reference, text)
        return clock.result(True, stdout=json.dumps(result, ensure_ascii=False))

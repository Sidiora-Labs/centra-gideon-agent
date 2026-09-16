"""Apply resolved workflow content to the artifact store while retaining user metadata."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import ActionClock

logger = logging.getLogger(__name__)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_KINDS = ("widget", "document", "report", "data")


def _stringify(value: Any) -> str:
    encoder = json.JSONEncoder(
        indent=2, sort_keys=True, ensure_ascii=False, default=str
    )
    try:
        return encoder.encode(value)
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True)
class _ArtifactWrite:
    slug: str
    content: str
    kind: str

    @classmethod
    def parse(cls, config: dict[str, Any]) -> _ArtifactWrite:
        slug = str(config.get("slug", "") or "").strip()
        if not slug:
            raise ValueError(
                "artifact-update is missing 'slug' — name the artifact to write"
            )
        if not _SLUG.match(slug):
            raise ValueError(
                f"artifact-update slug {slug!r} is not a valid id — use lowercase letters, digits and hyphens (it becomes a directory name)"
            )
        value = config.get("content")
        if value is None:
            raise ValueError(
                "artifact-update is missing 'content' — bind it to a node's output"
            )
        content = value if isinstance(value, str) else _stringify(value)
        kind = str(config.get("kind", "widget") or "widget")
        if kind not in _KINDS:
            raise ValueError(
                f"artifact-update kind {kind!r} must be one of: {', '.join(_KINDS)}"
            )
        return cls(slug, content, kind)

    def apply(
        self,
        store: Any,
        config: dict[str, Any],
        tags: list[str],
        snapshot: bool,
        collection: str,
    ) -> tuple[Any, bool]:
        try:
            existing = store.get(self.slug)
        except Exception:
            logger.debug("artifact-update: could not read %s", self.slug, exc_info=True)
            existing = None
        if existing is not None:
            updated = store.update(
                self.slug,
                content=self.content,
                snapshot=snapshot,
                event_type="iterated",
                actor="workflow",
                collection=collection or None,
            )
            return updated, False
        fields = dict(
            name=str(config.get("name", "") or self.slug),
            content=self.content,
            kind=self.kind,
            source="workflow",
            slug=self.slug,
            description=str(config.get("description", "") or ""),
            tags=tags,
            actor="workflow",
            collection=collection,
        )
        return store.create(**fields), True


class ArtifactUpdateActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "artifact-update"

    @property
    def display_name(self) -> str:
        return "Update Artifact"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        clock = ActionClock()
        try:
            request = _ArtifactWrite.parse(action_config)
        except ValueError as error:
            return ActionResult(False, error=str(error))
        try:
            from gideon.workspace.artifacts.registry import get_provider

            store = get_provider()
        except Exception as error:
            return ActionResult(False, error=f"artifact store unavailable: {error}")
        if store is None:
            return ActionResult(False, error="no artifact provider is registered")
        tags = [str(tag) for tag in (action_config.get("tags") or [])]
        snapshot = bool(action_config.get("snapshot", False))
        collection = str(action_config.get("collection", "") or "")
        try:
            artifact, created = request.apply(
                store, action_config, tags, snapshot, collection
            )
        except Exception as error:
            return clock.result(
                False,
                error=f"artifact-update could not write {request.slug!r}: {error}",
            )
        if artifact is None:
            return clock.result(
                False,
                error=f"artifact-update: the store did not write {request.slug!r}",
            )
        version = getattr(artifact, "version", 0)
        rendered = f'{{"slug": "{request.slug}", "version": {version}, "created": {str(created).lower()}}}'
        return clock.result(True, stdout=rendered)

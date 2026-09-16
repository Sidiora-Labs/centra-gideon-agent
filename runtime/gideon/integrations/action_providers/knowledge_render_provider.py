"""Render sanitized report documents and publish authored and derived artifacts."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from gideon.cognition.knowledge import reports
from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,55}$")
EXPORT_SUFFIX = "-report"


@dataclass(frozen=True)
class _ArtifactWrite:
    slug: str
    body: str
    create_fields: dict[str, Any]
    update_fields: dict[str, Any]
    authored: bool

    def apply(self, store: Any) -> int:
        previous = _get(store, self.slug)
        if (
            self.authored
            and previous is not None
            and (previous.content or "") == self.body
        ):
            return int(getattr(previous, "version", 0) or 0)
        if previous is None:
            artifact = store.create(
                slug=self.slug,
                content=self.body,
                source="cron",
                actor="workflow",
                **self.create_fields,
            )
        else:
            artifact = store.update(
                self.slug,
                content=self.body,
                snapshot=self.authored,
                actor="workflow",
                **self.update_fields,
            )
        if artifact is None:
            role = "spec" if self.authored else "export"
            raise RuntimeError(
                f"the store did not write the {role} artifact {self.slug!r}"
            )
        return int(getattr(artifact, "version", 0) or 0)


def _write_spec(
    store: Any, slug: str, spec: dict[str, Any], cfg: dict[str, Any]
) -> int:
    collection = str(cfg.get("collection") or "")
    write = _ArtifactWrite(
        slug,
        reports.canonical_spec_text(spec),
        dict(
            name=str(cfg.get("name") or spec.get("title") or slug),
            kind="json",
            description=str(cfg.get("description") or ""),
            tags=[str(tag) for tag in cfg.get("tags") or ()],
            collection=collection,
        ),
        dict(event_type="edited", collection=collection or None),
        True,
    )
    return write.apply(store)


def _write_export(
    store: Any, slug: str, document: str, spec_version: int, cfg: dict[str, Any]
) -> int:
    collection = str(cfg.get("collection") or "")
    lineage = dict(derived_from=slug, spec_version=spec_version)
    write = _ArtifactWrite(
        slug + EXPORT_SUFFIX,
        document,
        dict(
            name=f"{cfg.get('name') or slug} (rendered)",
            kind="html",
            description=f"Derived export of the {slug!r} report spec. Regenerated, not authored.",
            tags=[str(tag) for tag in cfg.get("tags") or ()],
            collection=collection,
            event_metadata=lineage,
        ),
        dict(
            event_type="iterated", collection=collection or None, event_metadata=lineage
        ),
        False,
    )
    return write.apply(store)


@dataclass(frozen=True)
class _ReportPublication:
    spec: dict[str, Any]
    document: Any
    config: dict[str, Any]

    def receipt(self) -> dict[str, Any]:
        return dict(
            blocks=self.document.block_count,
            bytes=len(self.document.html),
            warnings=self.document.warnings,
        )

    def publish(self, store: Any, slug: str) -> dict[str, Any]:
        spec_version = _write_spec(store, slug, self.spec, self.config)
        export_version = _write_export(
            store, slug, self.document.html, spec_version, self.config
        )
        return {
            **self.receipt(),
            "spec_slug": slug,
            "spec_version": spec_version,
            "export_slug": slug + EXPORT_SUFFIX,
            "export_version": export_version,
        }


def _get(store: Any, slug: str) -> Any:
    try:
        return store.get(slug)
    except Exception:
        logger.debug("render-report: could not read %s", slug, exc_info=True)
        return None


def _data(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _truthy(raw: Any) -> bool:
    return (
        raw
        if isinstance(raw, bool)
        else str(raw).strip().lower() in {"true", "1", "yes"}
    )


class KnowledgeRenderReportActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "render-report"

    @property
    def display_name(self) -> str:
        return "Render Report"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        started = time.monotonic()
        config = action_config or {}

        def finish(
            payload: dict[str, Any] | None = None, *, error: str = ""
        ) -> ActionResult:
            return ActionResult(
                not error,
                error=error,
                stdout=(
                    json.dumps(payload, ensure_ascii=False)
                    if payload is not None
                    else ""
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        raw = config.get("spec")
        if raw is None:
            return ActionResult(
                False,
                error="render-report is missing 'spec' — the declarative report to render",
            )
        try:
            spec = reports.parse_spec(raw)
            publication = _ReportPublication(
                spec, reports.render_report(spec, _data(config.get("data"))), config
            )
        except reports.SpecError as exc:
            return finish(error=f"render-report: {exc}")
        if _truthy(config.get("render_only")):
            return finish({**publication.receipt(), "html": publication.document.html})
        slug = str(config.get("slug") or "").strip()
        if not slug:
            return ActionResult(
                False,
                error="render-report is missing 'slug' — name the report to write",
            )
        if not _SLUG.match(slug):
            return ActionResult(
                False,
                error=f"render-report slug {slug!r} is not a valid id — use lowercase letters, digits and hyphens (it becomes a directory name)",
            )
        try:
            from gideon.workspace.artifacts.registry import get_provider

            store = get_provider()
        except Exception as exc:
            return ActionResult(False, error=f"artifact store unavailable: {exc}")
        if store is None:
            return ActionResult(False, error="no artifact provider is registered")
        try:
            payload = publication.publish(store, slug)
        except Exception as exc:
            return finish(error=f"render-report could not write {slug!r}: {exc}")
        return finish(payload)

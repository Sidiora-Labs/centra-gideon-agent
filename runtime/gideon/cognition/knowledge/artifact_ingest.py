"""Maintain searchable, redacted artifact mirrors through the knowledge source APIs."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)
ARTIFACT_SOURCE_PROVIDER = "artifacts"
ARTIFACT_SOURCE_KIND = "artifact"
ARTIFACT_SOURCE_NAME = "Artifacts"
ARTIFACT_SOURCE_URI = "artifact://"
ARTIFACT_ITEM_TYPE = "artifact"
INDEXABLE_KINDS = {
    "html": ".html",
    "markdown": ".md",
    "text": ".txt",
    "json": ".json",
    "csv": ".csv",
    "document": ".md",
}
MAX_MIRROR_CHARS = 400_000


def indexable_kind(kind: str) -> bool:
    normalized = (kind or "").strip().lower()
    return normalized in INDEXABLE_KINDS


def extract(kind: str, content: str) -> tuple[str, dict]:
    from gideon.cognition.knowledge.readers import html_to_prose

    extension = INDEXABLE_KINDS[(kind or "").strip().lower()]
    body = html_to_prose(content or "") if extension == ".html" else (content or "")
    metadata = dict(
        format=extension.lstrip("."),
        extension=extension,
        truncated=len(body) > MAX_MIRROR_CHARS,
    )
    return body[:MAX_MIRROR_CHARS], metadata


def redact(text: str) -> str:
    from gideon.security.security import redact_credentials, redact_exfiltration_urls

    value = text or ""
    for scrub in (redact_exfiltration_urls, redact_credentials):
        value, _ = scrub(value)
    return value


def mirror_sha(title: str, text: str) -> str:
    parts = [(value or "").encode("utf-8", "replace") for value in (title, text)]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def find_source(store: Any) -> dict | None:
    return next(
        (
            row
            for row in store.list_sources()
            if row.get("provider") == ARTIFACT_SOURCE_PROVIDER
        ),
        None,
    )


def ensure_source(store: Any) -> tuple[str, bool]:
    from gideon.integrations.knowledge_providers.base import ENRICHMENT_RAW

    row = find_source(store)
    if row is not None:
        return str(row["id"]), False
    fields = dict(
        name=ARTIFACT_SOURCE_NAME,
        provider=ARTIFACT_SOURCE_PROVIDER,
        kind=ARTIFACT_SOURCE_KIND,
        spec={"uri": ARTIFACT_SOURCE_URI},
        enrichment=ENRICHMENT_RAW,
        item_type=ARTIFACT_ITEM_TYPE,
        poll_interval_secs=0,
        created_by="system",
    )
    return store.create_source(**fields), True


@dataclass(frozen=True)
class _ArtifactMirror:
    title: str
    text: str
    digest: str
    metadata: dict

    @classmethod
    def read(cls, artifact, slug):
        body, details = extract(artifact.kind, artifact.content or "")
        title = redact((artifact.name or slug).strip() or slug)
        text = redact(body)
        digest = mirror_sha(title, text)
        return cls(
            title,
            text,
            digest,
            dict(
                artifact_slug=slug,
                artifact_kind=artifact.kind,
                artifact_sha=digest,
                **details,
            ),
        )


class ArtifactIndexer:
    INDEXED = "indexed"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    MISSING = "missing"
    DISABLED = "disabled"

    def __init__(
        self,
        store: Any,
        *,
        enqueue: Callable[[str], None] | None = None,
        provider_factory: Callable[[], Any] | None = None,
        config_loader: Callable[[], Any] | None = None,
    ) -> None:
        self._store, self._enqueue = store, enqueue
        self._provider_factory = provider_factory or self._default_provider
        self._config_loader = config_loader or self._load_config

    @staticmethod
    def _default_provider() -> Any:
        from gideon.workspace.artifacts import registry

        name = "native"
        return registry.get_provider(name)

    @staticmethod
    def _load_config() -> Any:
        from gideon.core.config.loader import AppConfig

        config = AppConfig.load()
        return config.knowledge

    def enabled(self) -> bool:
        try:
            config = self._config_loader()
            return bool(getattr(config, "auto_ingest_artifacts", True))
        except Exception:
            logger.debug("Artifact indexing configuration unavailable", exc_info=True)
            return True

    def listener(self, change: str, slug: str) -> None:
        from gideon.workspace.artifacts.changes import DELETE, UPSERT

        dispatch = {DELETE: self.remove, UPSERT: self.index}
        handler = dispatch.get(change)
        if handler is None:
            logger.warning("Unhandled artifact change %r for %s", change, slug)
        else:
            handler(slug)

    def index(self, slug: str) -> str:
        if not self.enabled():
            return self.DISABLED
        provider = self._provider_factory()
        artifact = provider.get(slug) if provider is not None else None
        if artifact is None:
            return self.MISSING
        if not indexable_kind(artifact.kind):
            self.remove(slug)
            return self.SKIPPED
        source_id, first_write = ensure_source(self._store)
        mirror = _ArtifactMirror.read(artifact, slug)
        existing = self._store.find_source_item(source_id, slug)
        result = self._write(
            source_id,
            slug,
            existing,
            mirror.title,
            mirror.text,
            artifact,
            mirror.digest,
            mirror.metadata,
        )
        if first_write:
            self.backfill()
        return result

    def _write(
        self,
        source_id: str,
        slug: str,
        existing: dict | None,
        title: str,
        text: str,
        art: Any,
        sha: str,
        file_metadata: dict,
    ) -> str:
        summary = redact((art.description or "").strip())
        fields = dict(title=title, content=text, summary=summary)
        metadata = dict(processing_status="queued", file_metadata=file_metadata)
        if existing is None:
            item_id = self._store.create_typed_item(
                **fields,
                item_type=ARTIFACT_ITEM_TYPE,
                provider=ARTIFACT_SOURCE_PROVIDER,
                source_id=source_id,
                guid=slug,
                extra=metadata,
            )
            if item_id is None:
                return self.UNCHANGED
        else:
            if sha == (existing.get("file_metadata") or {}).get("artifact_sha"):
                return self.UNCHANGED
            item_id = existing["id"]
            self._store.update_item(item_id, **fields, **metadata)
        self._enqueue_item(item_id)
        return self.INDEXED

    def remove(self, slug: str) -> bool:
        row = find_source(self._store)
        return (
            False
            if row is None
            else bool(self._store.forget_source_item(str(row["id"]), slug))
        )

    def backfill(self) -> int:
        provider = self._provider_factory()
        if provider is None:
            return 0
        outcomes = 0
        for artifact in provider.list():
            if indexable_kind(artifact.kind):
                try:
                    result = self.index(artifact.slug)
                except Exception:
                    logger.warning(
                        "Artifact %s indexing failed", artifact.slug, exc_info=True
                    )
                else:
                    outcomes += result == self.INDEXED
        return outcomes

    def _enqueue_item(self, item_id: str) -> None:
        if self._enqueue is not None:
            try:
                self._enqueue(item_id)
            except Exception:
                logger.debug(
                    "Artifact mirror queue rejected %s", item_id, exc_info=True
                )


def start(
    store: Any, *, enqueue: Callable[[str], None] | None = None
) -> ArtifactIndexer:
    from gideon.workspace.artifacts import changes

    mirror = ArtifactIndexer(store, enqueue=enqueue)
    changes.subscribe(mirror.listener)
    if mirror.enabled() and ensure_source(store)[1]:
        count = mirror.backfill()
        logger.info("Artifact mirror startup indexed %d artifacts", count)
    return mirror

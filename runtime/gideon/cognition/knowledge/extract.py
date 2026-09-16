"""Attachment extraction through the shared media graph without persistence."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class _AttachmentResult:
    def __init__(self, path, kind, result):
        self.path, self.kind, self.result = path, kind, result

    def text(self) -> str:
        merged = self.result.outputs.get("consolidate")
        if merged is not None and merged.success:
            body = merged.text or ""
        else:
            pool = self.result.pooled_outputs()
            body = pool[0].text if pool else ""
        return body.strip() or self.description()

    def description(self) -> str:
        metadata = {}
        for output in self.result.outputs.values():
            metadata.update(output.metadata or {})
        details = []
        if metadata.get("width") and metadata.get("height"):
            details.append(f"{metadata['width']}×{metadata['height']}")
        formatters = (
            ("format", str),
            ("page_count", lambda count: f"{count} pages"),
            ("duration_seconds", lambda seconds: f"{round(float(seconds))}s"),
        )
        details.extend(
            render(metadata[key]) for key, render in formatters if metadata.get(key)
        )
        try:
            size = os.path.getsize(self.path) / 1024
            details.append(f"{size:.0f} KB" if size < 1024 else f"{size / 1024:.1f} MB")
        except OSError:
            pass
        if not details:
            return ""
        return (
            f"{(self.kind or 'file').capitalize()}: {os.path.basename(self.path)} "
            f"({', '.join(details)}) — no extractable text content."
        )


async def extract_file_content(file_path: str, mime: str | None = None) -> str:
    if not file_path or not os.path.isfile(file_path):
        return ""
    from gideon.cognition.knowledge import media
    from gideon.cognition.knowledge.pipeline import (
        NodeContext,
        ensure_nodes_registered,
        graph_for,
    )
    from gideon.cognition.knowledge.pipeline.executor import PipelineExecutor

    ensure_nodes_registered()
    kind = media.classify(os.path.basename(file_path), mime) or "document"
    try:
        graph = graph_for(kind)
    except Exception:
        logger.warning("Attachment graph unavailable for %s", kind, exc_info=True)
        return ""
    context = NodeContext(
        item_id=f"attachment:{os.path.basename(file_path)}",
        item_type=kind,
        file_path=file_path,
        content="",
        url="",
    )
    try:
        result = await PipelineExecutor(graph).run(context)
    except Exception:
        logger.warning("Attachment extraction failed for %s", file_path, exc_info=True)
        return ""
    return _AttachmentResult(file_path, kind, result).text()


def _structural_descriptor(file_path: str, item_type: str, result) -> str:
    return _AttachmentResult(file_path, item_type, result).description()

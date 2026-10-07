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
        metadata: dict = {}
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
        from gideon.cognition.knowledge.pipeline.outcomes import told

        phases = {
            name: outcome.to_dict() for name, outcome in self.result.outcomes.items()
        }
        reasons = told(phases)
        if not details:
            return " ".join(reasons)
        suffix = (
            "Text extraction is incomplete. " + " ".join(reasons)
            if reasons
            else "No extractable text content."
        )
        return (
            f"{(self.kind or 'file').capitalize()}: {os.path.basename(self.path)} "
            f"({', '.join(details)}) — {suffix}"
        )


async def extract_file_content(
    file_path: str,
    mime: str | None = None,
    *,
    expected_stamp: tuple[int, ...] | None = None,
) -> str:
    from gideon.cognition.knowledge import media
    from gideon.cognition.knowledge.pipeline import (
        NodeContext,
        ensure_nodes_registered,
        graph_for,
    )
    from gideon.cognition.knowledge.pipeline.executor import PipelineExecutor
    from gideon.workspace.uploads.content_intake import (
        IntakeRefused,
        approve_path,
        approve_text,
        source_stamp,
    )

    if not file_path:
        return ""
    from gideon.security.security import is_sensitive_path

    if is_sensitive_path(file_path):
        raise PermissionError("Sensitive files cannot be extracted.")
    stamp = expected_stamp if expected_stamp is not None else source_stamp(file_path)
    snapshot = await approve_path(
        file_path, mime, surface="attachment_extraction", expected_stamp=stamp
    )
    try:
        ensure_nodes_registered()
        kind = media.classify(os.path.basename(file_path), mime) or "document"
        graph = graph_for(kind)
        async with snapshot.reader_path() as reader_path:
            content = ""
            if kind == "gist":
                import asyncio

                from gideon.cognition.knowledge.readers import FileReader

                read = asyncio.create_task(
                    asyncio.to_thread(FileReader().read_approved, snapshot)
                )
                try:
                    content, metadata = await asyncio.shield(read)
                except asyncio.CancelledError:
                    await read
                    raise
                if metadata.get("format") == "error":
                    return "Text extraction failed: " + str(
                        metadata.get("error", "File could not be read.")
                    )
                content = (
                    await approve_text(content, surface="attachment_extraction")
                ).text
            context = NodeContext(
                item_id=f"attachment:{os.path.basename(file_path)}",
                item_type=kind,
                file_path=reader_path,
                content=content,
                url="",
                params={"_approved_source": snapshot, "_content_scan_required": True},
            )
            result = await PipelineExecutor(graph).run(context)
            for output in result.outputs.values():
                code = output.metadata.get("content_refusal")
                if code:
                    raise IntakeRefused(
                        code,
                        output.error,
                        422 if code == "upload_content_refused" else 503,
                    )
            if source_stamp(file_path) != stamp:
                raise IntakeRefused(
                    "upload_content_changed",
                    "The file changed during extraction, so its text was withheld. Please attach it again.",
                    409,
                )
            text = _AttachmentResult(file_path, kind, result).text()
            return (
                await approve_text(text, surface="attachment_extraction")
            ).scanned_head
    finally:
        snapshot.close()


def _structural_descriptor(file_path: str, item_type: str, result) -> str:
    return _AttachmentResult(file_path, item_type, result).description()

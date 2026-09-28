"""In-process registry for chat-attachment content extraction.

When a file is attached in chat it's uploaded to ``~/.gideon/uploads`` and
non-image extraction (knowledge EXTRACTION graph only — see ``knowledge.extract``)
starts while the user is still typing. Image extraction waits until a text preview
or a text-only chat model needs it. The result is cached by saved file path.

Singleton, keyed by absolute upload path. Bounded so a long session can't grow
it without bound.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 200
_MAX_TEXT_CHARS = 200_000


class AttachmentExtractor:
    """Fires-and-tracks requested extraction tasks keyed by upload path."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[str]] = {}

    def start(self, path: str, mime: str | None = None) -> None:
        """Begin non-image extraction now; images wait until text is requested."""
        if not path or path in self._tasks:
            return
        from gideon.interfaces.dashboard.attachment_images import is_image_attachment

        if is_image_attachment(path):
            return
        self._begin(path, mime)

    def _begin(self, path: str, mime: str | None = None) -> None:
        if not path or path in self._tasks:
            return
        if len(self._tasks) >= _MAX_ENTRIES:
            for k in [k for k, t in list(self._tasks.items()) if t.done()][:50]:
                self._tasks.pop(k, None)
        try:
            self._tasks[path] = asyncio.create_task(self._run(path, mime))
        except RuntimeError:
            logger.debug("attachment extract: no loop to start task for %s", path)

    async def _run(self, path: str, mime: str | None) -> str:
        from gideon.cognition.knowledge.extract import extract_file_content

        try:
            text = await extract_file_content(path, mime)
        except Exception:
            logger.warning("attachment extract failed for %s", path, exc_info=True)
            return ""
        cleaned = (text or "").replace(os.path.basename(path), display_name(path))
        return cleaned[:_MAX_TEXT_CHARS]

    async def get(self, path: str, mime: str | None = None) -> str:
        """Await + return the extracted text for *path*. Starts extraction if it
        wasn't already kicked off at upload (so a late/missed start still works).
        Blocks until extraction completes — this is the turn-gating point."""
        from gideon.interfaces.dashboard.attachment_images import is_image_attachment

        is_image = is_image_attachment(path)
        if is_image:
            from gideon.extensions.providers.image_input import image_reader

            reader = await image_reader()
            if not reader.ref:
                return f"Image: {display_name(path)} — {reader.reason or 'No image model is set up.'}"
        if path not in self._tasks:
            self._begin(path, mime)
        task = self._tasks.get(path)
        if task is None:
            from gideon.cognition.knowledge.extract import extract_file_content

            try:
                text = await extract_file_content(path, mime)
                cleaned = (text or "").replace(os.path.basename(path), display_name(path))
                return cleaned[:_MAX_TEXT_CHARS]
            except Exception:
                return ""
        try:
            return await task
        except Exception:
            return ""


_INSTANCE: AttachmentExtractor | None = None


def get_extractor() -> AttachmentExtractor:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AttachmentExtractor()
    return _INSTANCE


def display_name(path: str) -> str:
    """Clean filename for prompt labelling — strips the uuid upload prefix."""
    base = os.path.basename(path)
    if (
        len(base) > 33
        and base[32] == "_"
        and all(c in "0123456789abcdef" for c in base[:32])
    ):
        return base[33:]
    return base

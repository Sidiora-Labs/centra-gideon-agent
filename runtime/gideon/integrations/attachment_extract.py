"""In-process registry for chat-attachment content extraction.

When a file is attached in chat it's uploaded to ``~/.gideon/uploads`` and
non-image extraction (knowledge EXTRACTION graph only — see ``knowledge.extract``)
starts while the user is still typing. Image extraction waits until a text preview
or a text-only chat model needs it. The result is cached by saved file identity and refreshed when its bytes change.

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
    """Extraction tasks bound to a source identity, never a reusable path alone."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[str]] = {}
        self._fingerprints: dict[str, tuple[int, ...]] = {}
        self._mimes: dict[str, str | None] = {}

    def start(self, path: str, mime: str | None = None) -> None:
        from gideon.integrations.attachment_images import is_image_attachment

        if not path or is_image_attachment(path):
            return
        self._begin(path, mime)

    def _begin(self, path: str, mime: str | None = None) -> None:
        from gideon.workspace.uploads.content_intake import source_stamp

        if not path:
            return
        try:
            stamp = source_stamp(path)
        except OSError:
            return
        if (
            self._fingerprints.get(path) == stamp
            and self._mimes.get(path) == mime
            and path in self._tasks
        ):
            return
        prior = self._tasks.pop(path, None)
        if prior is not None and not prior.done():
            prior.cancel()
        self._fingerprints.pop(path, None)
        self._mimes.pop(path, None)
        if len(self._tasks) >= _MAX_ENTRIES:
            for key in [key for key, task in self._tasks.items() if task.done()][:50]:
                self._tasks.pop(key, None)
                self._fingerprints.pop(key, None)
                self._mimes.pop(key, None)
            if len(self._tasks) >= _MAX_ENTRIES:
                return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._run(path, mime, stamp))
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        self._tasks[path] = task
        self._fingerprints[path] = stamp
        self._mimes[path] = mime

    async def _run(self, path: str, mime: str | None, stamp: tuple[int, ...]) -> str:
        from gideon.cognition.knowledge.extract import extract_file_content

        text = await extract_file_content(path, mime, expected_stamp=stamp)
        return (text or "").replace(os.path.basename(path), display_name(path))[
            :_MAX_TEXT_CHARS
        ]

    async def get(
        self, path: str, mime: str | None = None, *, strict: bool = False
    ) -> str:
        from gideon.integrations.attachment_images import is_image_attachment
        from gideon.workspace.uploads.content_intake import IntakeRefused, source_stamp

        image = is_image_attachment(path)
        try:
            # The caller's original read boundary remains in force even for cached text.
            from gideon.security.security import is_sensitive_path

            if is_sensitive_path(path):
                raise PermissionError("Sensitive files cannot be extracted.")
            stamp = source_stamp(path)
            if image:
                from gideon.extensions.providers.image_input import image_reader

                reader = await image_reader()
                if not reader.ref:
                    return f"Image: {display_name(path)} — {reader.reason or 'No image model is set up.'}"
            self._begin(path, mime)
            task = self._tasks.get(path)
            if task is None:
                text = await self._run(path, mime, stamp)
            else:
                text = await asyncio.shield(task)
            if source_stamp(path) != stamp:
                raise IntakeRefused(
                    "upload_content_changed",
                    "The file changed during extraction, so its text was withheld. Please attach it again.",
                    409,
                )
            return text
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            refused = IntakeRefused(
                "upload_content_changed",
                "The file changed during extraction, so its text was withheld. Please attach it again.",
                409,
            )
        except PermissionError:
            refused = IntakeRefused(
                "content_read_denied",
                "This file cannot be read under the current file permissions.",
                403,
            )
        except IntakeRefused as error:
            refused = error
        except OSError:
            refused = IntakeRefused(
                "upload_content_unchecked",
                "The file could not be read, so no text was supplied.",
                503,
            )
        except Exception:
            refused = IntakeRefused(
                "upload_content_unchecked",
                "Text extraction could not finish, so no text was supplied.",
                503,
            )
        if strict:
            raise refused
        prefix = "Image" if image else "File"
        return f"{prefix}: {display_name(path)} — {refused.message}"


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

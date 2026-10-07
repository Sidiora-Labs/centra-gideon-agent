"""Approved upload persistence using the native logical-document item schema."""

from __future__ import annotations

import asyncio
import codecs
from pathlib import Path
from uuid import uuid4

from gideon.cognition.knowledge.media import (
    classify,
    code_language,
    guess_mime,
    make_image_thumbnail,
)
from gideon.workspace.uploads.content_intake import (
    ApprovedFile,
    IntakeRefused,
    approve_text,
)


def _code(snapshot: ApprovedFile) -> str:
    raw = snapshot.read_bytes()
    for mark, codec in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if raw.startswith(mark):
            return raw.decode(codec, errors="replace")
    if b"\0" in raw[:8192]:
        raise IntakeRefused(
            "upload_type_unsupported",
            "The file is binary, so it cannot be kept as source code.",
            415,
        )
    return raw.decode("utf-8", errors="replace")


async def _owned_io(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def store_approved_file(
    store, snapshot: ApprovedFile, *, tags: list[str] | None = None
) -> tuple[dict | None, bool]:
    """Store the exact approved bytes or decoded/scanned code, with native dedup."""
    snapshot.require_approved()
    filename, mime = snapshot.filename, snapshot.mime
    item_type = classify(filename, mime)
    if item_type is None:
        raise IntakeRefused(
            "upload_type_unsupported",
            "This file type is not supported by Knowledge.",
            415,
        )
    language = code_language(filename)
    if item_type == "gist" and language:
        text = await _owned_io(_code, snapshot)
        text = (await approve_text(text, surface="knowledge")).text
        existing = store.find_active_by_file_hash(snapshot.digest)
        if existing:
            return existing, False
        identifier = store.create_typed_item(
            item_type="gist",
            title=filename,
            content=text,
            tags=tags,
            extra={
                "gist_language": language,
                "file_metadata": {"content_hash": snapshot.digest},
                "processing_status": "queued",
            },
        )
        return store.get_item(identifier), True

    from gideon.cognition.knowledge import knowledge_files_dir

    identifier = str(uuid4())
    directory = Path(knowledge_files_dir())
    destination = directory / (identifier + Path(filename).suffix.lower())
    thumbnail = directory / (identifier + ".thumb.webp")
    thumbnail_path = ""
    retained = False
    try:
        await snapshot.persist(destination)
        if item_type == "image" and await _owned_io(
            make_image_thumbnail, str(destination), str(thumbnail)
        ):
            thumbnail_path = str(thumbnail)
        # No await between the final dedup check and the native row creation:
        # two uploads finishing on this loop cannot create duplicate documents.
        existing = store.find_active_by_file_hash(snapshot.digest)
        if existing:
            return existing, False
        guessed = guess_mime(filename)
        mime_type = (
            mime if mime and mime.split("/", 1)[0].lower() == item_type else guessed
        )
        new_id = store.create_typed_item(
            item_type=item_type,
            title=filename,
            content="",
            tags=tags,
            extra={
                "file_path": str(destination),
                "mime_type": mime_type,
                "file_size": snapshot.size,
                "thumbnail_path": thumbnail_path,
                "file_metadata": {
                    "content_hash": snapshot.digest,
                    "original_filename": filename,
                },
                "processing_status": "queued",
            },
        )
        item = store.get_item(new_id)
        retained = item is not None
        return item, True
    finally:
        if not retained:
            destination.unlink(missing_ok=True)
            thumbnail.unlink(missing_ok=True)

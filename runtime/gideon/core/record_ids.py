"""Validate store record names and enforce resolved filesystem containment."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "MAX_RECORD_ID_LEN",
    "UnsafeRecordId",
    "is_safe_record_id",
    "record_path",
    "require_safe_record_id",
]
MAX_RECORD_ID_LEN = 200
_SEPARATORS = ("/", "\\", os.sep, os.altsep or "/")


class UnsafeRecordId(Exception):
    """A store address was refused; callers must distinguish this from absence."""


def is_safe_record_id(record_id: object) -> bool:
    if not isinstance(record_id, str):
        return False
    forbidden = set(_SEPARATORS) | {"\x00"}
    valid_shape = 0 < len(record_id) <= MAX_RECORD_ID_LEN and record_id not in {
        ".",
        "..",
    }
    if not valid_shape or any(
        separator in record_id for separator in forbidden if separator
    ):
        return False
    return not (Path(record_id).is_absolute() or os.path.splitdrive(record_id)[0])


def require_safe_record_id(record_id: object, *, kind: str = "id") -> str:
    if is_safe_record_id(record_id):
        return str(record_id)
    display = record_id if isinstance(record_id, str) else type(record_id).__name__
    message = f"{kind} must be a single path segment with no separators, no '..' and at most {MAX_RECORD_ID_LEN} characters — got {display!r}"
    raise UnsafeRecordId(message)


def record_path(
    root: Path,
    record_id: object,
    *,
    prefix: str = "",
    suffix: str = ".json",
    kind: str = "id",
) -> Path:
    name = require_safe_record_id(record_id, kind=kind)
    destination = root.joinpath("".join((prefix, name, suffix)))
    actual, boundary = destination.resolve(strict=False), root.resolve(strict=False)
    if not actual.is_relative_to(boundary):
        raise UnsafeRecordId(
            f"{kind} {name!r} resolves outside its store ({actual} not under {boundary})"
        )
    return destination

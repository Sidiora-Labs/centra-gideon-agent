"""Shared JSON request parsing and field validation."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web


class RequestValidationError(ValueError):
    """The request body or field is not admissible."""


class RequestBodyTypeError(RequestValidationError):
    """The JSON body is valid but is not an object."""


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise RequestValidationError(f"{field} must be a string")
    return value


def optional_string(value: Any, field: str) -> str:
    if value is None:
        return ""
    return require_string(value, field)


def string_field(body: dict[str, Any], field: str, *, required: bool = False) -> str:
    value = body.get(field)
    return (
        require_string(value, field) if required else optional_string(value, field)
    ).strip()


async def read_json_body(request: web.Request) -> dict[str, Any]:
    """Read an optional JSON object body through the shared request boundary."""
    raw = await request.read()
    if not raw.strip():
        return {}
    if (
        request.content_type != "application/json"
        and not request.content_type.endswith("+json")
    ):
        raise RequestValidationError("content type must be application/json")
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RequestValidationError("body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise RequestBodyTypeError("body must be a JSON object")
    return body

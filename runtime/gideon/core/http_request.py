"""Shared JSON request parsing and field validation."""

from __future__ import annotations

import json
from typing import Any, overload

from aiohttp import web


class RequestValidationError(ValueError):
    """The request body or field is not admissible."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code if message is not None else "bad_request"
        self.message = code if message is None else message
        super().__init__(self.message)


class _Missing:
    __slots__ = ()


MISSING = _Missing()


@overload
def bool_field(body: dict[str, Any], field: str, *, default: bool) -> bool: ...


@overload
def bool_field(body: dict[str, Any], field: str, *, default: None) -> bool | None: ...


def bool_field(body: dict[str, Any], field: str, *, default: bool | None) -> bool | None:
    if field not in body or (default is None and body[field] is None):
        return default
    return _boolean(field, body[field])


def require_bool(body: dict[str, Any], field: str) -> bool:
    if field not in body:
        raise RequestValidationError("field_required", f"{field} is required (true or false).")
    return _boolean(field, body[field])


def optional_bool(body: dict[str, Any], field: str) -> bool | _Missing:
    return MISSING if field not in body else require_bool(body, field)


def _boolean(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    kinds = {type(None): "null", str: "a string", int: "a number", float: "a number",
             list: "an array", dict: "an object"}
    raise RequestValidationError(
        "field_not_a_boolean",
        f"{field} must be true or false (a JSON boolean), not {kinds.get(type(value), type(value).__name__)}.",
    )


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

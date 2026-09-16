"""Gateway failures represented consistently across Python integrations."""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"


class GideonError(Exception):
    def __init__(self, code: ErrorCode | str, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.code = ErrorCode(code)
        self.status, self.body = status, body

    def to_dict(self) -> dict[str, Any]:
        attributes = {"code": self.code.value, "message": str(self)}
        for name in ("status", "body"):
            value = getattr(self, name)
            if value is not None:
                attributes[name] = value
        return attributes


_STATUS_ERRORS = {401: ErrorCode.AUTH_EXPIRED, 403: ErrorCode.AUTH_EXPIRED,
                  404: ErrorCode.NOT_FOUND, 429: ErrorCode.RATE_LIMITED}


def http_status_to_code(status: int) -> ErrorCode:
    fallback = ErrorCode.SERVER_ERROR if status >= 500 else ErrorCode.VALIDATION_ERROR
    return _STATUS_ERRORS.get(status, fallback)


def http_error(status: int, body: Any = None) -> GideonError:
    message = f"HTTP {status}"
    if isinstance(body, dict) and "error" in body:
        message = str(body["error"])
    elif isinstance(body, str):
        message = body
    return GideonError(http_status_to_code(status), message, status, body)

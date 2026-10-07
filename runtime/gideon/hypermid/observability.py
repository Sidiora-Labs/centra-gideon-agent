"""Typed push metadata and bounded, redacted Hypermid logging."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Mapping

_LOGGER = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WAKE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+")
_SENSITIVE = {
    "authorization",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "private_key",
    "certificate_key",
}


class LogLevel(IntEnum):
    TRACE = 0
    DEBUG = 1
    INFO = 2
    WARN = 3
    ERROR = 4
    OFF = 5

    @classmethod
    def parse(cls, value: str) -> LogLevel:
        normalized = value.strip().upper()
        if normalized == "WARNING":
            normalized = "WARN"
        try:
            return cls[normalized]
        except KeyError as error:
            raise ValueError("invalid Hypermid log level") from error


@dataclass(frozen=True, slots=True)
class LogFilter:
    rules: tuple[tuple[str, LogLevel], ...]
    malformed: bool = False

    @classmethod
    def parse(cls, value: str | None) -> LogFilter:
        rules: list[tuple[str, LogLevel]] = []
        malformed = False
        for item in filter(None, (part.strip() for part in (value or "").split(","))):
            try:
                prefix, level = item.rsplit("=", 1)
                if prefix != "*" and _LOGGER.fullmatch(prefix) is None:
                    raise ValueError
                rules.append((prefix, LogLevel.parse(level)))
            except ValueError:
                malformed = True
        rules.sort(key=lambda item: len(item[0]), reverse=True)
        return cls(tuple(rules), malformed)

    def level_for(self, logger: str) -> LogLevel:
        for prefix, level in self.rules:
            if prefix == "*" or logger == prefix or logger.startswith(prefix + "."):
                return level
        return LogLevel.INFO

    def enabled(self, logger: str, level: LogLevel) -> bool:
        threshold = self.level_for(logger)
        return threshold != LogLevel.OFF and level >= threshold


@dataclass(frozen=True, slots=True)
class LogRecord:
    timestamp: str
    level: LogLevel
    logger: str
    message: str
    bound: tuple[tuple[str, str], ...] = ()
    fields: Mapping[str, Any] = field(default_factory=dict)


class LogParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    secrets: tuple[str, ...] = ()

    def text(self, value: str) -> str:
        result = value
        for secret in sorted(filter(None, self.secrets), key=len, reverse=True):
            result = result.replace(secret, "[REDACTED]")
        result = _BEARER.sub("Bearer [REDACTED]", result)
        return _ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", result)


def redact_record(
    record: LogRecord,
    fleet: RedactionPolicy,
    module_redactor: Callable[[str], str] | None = None,
) -> LogRecord:
    def text(value: str) -> str:
        redacted = fleet.text(value)
        if module_redactor is not None:
            redacted = module_redactor(redacted)
        return guard_controls(redacted)

    fields = {
        key: _redact_value(value, text)
        for key, value in record.fields.items()
        if key.lower() not in _SENSITIVE
    }
    return LogRecord(
        timestamp=record.timestamp,
        level=record.level,
        logger=record.logger,
        message=text(record.message),
        bound=tuple(
            (key, text(value))
            for key, value in record.bound
            if key.lower() not in _SENSITIVE
        ),
        fields=fields,
    )


def _redact_value(value: Any, redact: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item, redact) for item in value]
    if isinstance(value, Mapping):
        return {key: _redact_value(item, redact) for key, item in value.items()}
    return value


def guard_controls(value: str) -> str:
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(
        (
            character
            if not (ord(character) < 32 or ord(character) == 127)
            else f"\\u{ord(character):04x}"
        )
        for character in value
    )


def format_line(record: LogRecord) -> bytes:
    _validate_record(record)
    document: dict[str, Any] = {
        "timestamp": record.timestamp,
        "level": record.level.name.lower(),
        "logger": record.logger,
    }
    if record.bound:
        document["bound"] = [
            {"key": key, "value": value} for key, value in record.bound
        ]
    document["message"] = record.message
    if record.fields:
        document["fields"] = dict(sorted(record.fields.items()))
    return (
        json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def parse_line(line: bytes) -> LogRecord:
    if b"\n" in line.rstrip(b"\n") or b"\r" in line:
        raise LogParseError("embedded_newline")
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LogParseError("invalid_json") from error
    if not isinstance(value, Mapping) or set(value) - {
        "timestamp",
        "level",
        "logger",
        "bound",
        "message",
        "fields",
    }:
        raise LogParseError("invalid_shape")
    try:
        bound = tuple(
            (str(item["key"]), str(item["value"]))
            for item in value.get("bound", [])
            if isinstance(item, Mapping)
        )
        fields = value.get("fields", {})
        if not isinstance(fields, Mapping):
            raise ValueError
        record = LogRecord(
            timestamp=str(value["timestamp"]),
            level=LogLevel.parse(str(value["level"])),
            logger=str(value["logger"]),
            message=str(value["message"]),
            bound=bound,
            fields=dict(fields),
        )
        _validate_record(record)
        return record
    except (KeyError, TypeError, ValueError) as error:
        raise LogParseError("invalid_shape") from error


def _validate_record(record: LogRecord) -> None:
    if (
        len(record.timestamp) < 20
        or record.timestamp[4:5] != "-"
        or record.timestamp[7:8] != "-"
        or record.timestamp[10:11] != "T"
        or not record.timestamp.endswith("Z")
    ):
        raise LogParseError("invalid_timestamp")
    if _LOGGER.fullmatch(record.logger) is None:
        raise LogParseError("invalid_logger")
    if "\n" in record.message or "\r" in record.message:
        raise LogParseError("embedded_newline")


@dataclass(slots=True)
class DailySegmentWriter:
    root: Path
    maximum_bytes: int
    oversized_reported: bool = False

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def append(self, utc_date: str, line: bytes) -> bool:
        _require_date(utc_date)
        _require_line(line)
        path = self.root / f"hypermid-{utc_date}.log"
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            written = os.write(descriptor, line)
            if written != len(line):
                raise OSError("short append to daily log segment")
            size = os.fstat(descriptor).st_size
        finally:
            os.close(descriptor)
        if size > self.maximum_bytes and not self.oversized_reported:
            self.oversized_reported = True
            return False
        return True

    def prune_before(self, cutoff_utc_date: str) -> tuple[Path, ...]:
        _require_date(cutoff_utc_date)
        removed = []
        for path in self.root.iterdir():
            match = re.fullmatch(r"hypermid-(\d{4}-\d{2}-\d{2})\.log", path.name)
            if match is not None and match.group(1) < cutoff_utc_date:
                path.unlink()
                removed.append(path)
        return tuple(removed)


@dataclass(slots=True)
class CaptureSink:
    path: Path
    maximum_bytes: int
    generations: int = 2

    def write_line(self, line: bytes) -> None:
        _require_line(line)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        size = self.path.stat().st_size if self.path.exists() else 0
        if size and size + len(line) > self.maximum_bytes:
            self._rotate()
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if os.write(descriptor, line) != len(line):
                raise OSError("short capture log write")
        finally:
            os.close(descriptor)

    def _rotate(self) -> None:
        if self.generations == 0:
            self.path.unlink(missing_ok=True)
            return
        for generation in range(self.generations, 0, -1):
            destination = Path(f"{self.path}.{generation}")
            if generation == self.generations:
                destination.unlink(missing_ok=True)
            source = (
                self.path if generation == 1 else Path(f"{self.path}.{generation - 1}")
            )
            if source.exists():
                os.replace(source, destination)


@dataclass(slots=True)
class SinkHealth:
    fallback_reported: bool = False
    swallowed_writes: int = 0

    def record_failure(self) -> None:
        self.swallowed_writes += 1
        if not self.fallback_reported:
            self.fallback_reported = True
            print("hypermid.log fallback: log sink unavailable", file=sys.stderr)


@dataclass(frozen=True, slots=True)
class WakeToken:
    value: str

    def __post_init__(self) -> None:
        if _WAKE.fullmatch(self.value) is None:
            raise ValueError("wake token must be an opaque base64url token")

    def plaintext(self) -> bytes:
        return self.value.encode("ascii")


@dataclass(frozen=True, slots=True)
class PushEnvelopeHeader:
    version: int
    routing_id: bytes

    @classmethod
    def parse(cls, envelope: bytes) -> PushEnvelopeHeader:
        if len(envelope) < 57:
            raise ValueError("malformed push envelope")
        if envelope[0] not in (1, 2):
            raise ValueError("unsupported push envelope version")
        routing = bytes(envelope[1:9])
        if envelope[0] == 1 and routing != bytes(8):
            raise ValueError("anonymous envelope has authenticated routing data")
        return cls(envelope[0], routing)


def _require_date(value: str) -> None:
    if _DATE.fullmatch(value) is None:
        raise ValueError("UTC date must use YYYY-MM-DD")


def _require_line(value: bytes) -> None:
    if not value.endswith(b"\n") or b"\n" in value[:-1]:
        raise ValueError("write must contain exactly one complete line")


__all__ = [
    "CaptureSink",
    "DailySegmentWriter",
    "LogFilter",
    "LogLevel",
    "LogParseError",
    "LogRecord",
    "PushEnvelopeHeader",
    "RedactionPolicy",
    "SinkHealth",
    "WakeToken",
    "format_line",
    "guard_controls",
    "parse_line",
    "redact_record",
]

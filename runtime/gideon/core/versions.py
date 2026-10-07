"""Bounded PEP 440 parsing and ordering for app and release versions."""

from packaging.version import Version

_MAX_LENGTH = 128
_UNREADABLE = Version("0")


def parse_version(text: str) -> Version | None:
    if not isinstance(text, str) or len(text) > _MAX_LENGTH:
        return None
    try:
        return Version(text)
    except ValueError:
        return None


def is_newer(candidate: str, current: str) -> bool:
    new, now = parse_version(candidate), parse_version(current)
    return new is not None and now is not None and new > now


def same_version(a: str, b: str) -> bool:
    version = parse_version(a)
    return version is not None and version == parse_version(b)


def order_key(text: str) -> tuple[bool, Version]:
    version = parse_version(text)
    return (True, version) if version is not None else (False, _UNREADABLE)


def normalize_version(text: str) -> str:
    version = parse_version(text)
    return str(version) if version is not None else ""

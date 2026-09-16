"""Resolve authored, configured and machine timezone policies for every scheduler."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
LOCALTIME_LINK = Path("/etc/localtime")
TIMEZONE_FILE = Path("/etc/timezone")
_ZONEINFO_DIRS = ("zoneinfo", "zoneinfo.default")
SOURCE_EXPLICIT = "explicit"
SOURCE_CONFIG = "config"
SOURCE_MACHINE = "machine"
SOURCE_UTC_FALLBACK = "utc-fallback"
UTC_NAME = "UTC"


class UnknownTimeZone(ValueError):
    def __init__(self, name: str, *, where: str = ""):
        self.name, self.where = name, where
        context = f"{where}: " if where else ""
        message = (
            f"{context}unknown timezone {name!r} — not an IANA zone name. Use a name like "
            "'America/Los_Angeles' or 'Europe/London'; abbreviations such as 'PDT' or "
            "'CEST' are not IANA zones and cannot be resolved."
        )
        super().__init__(message)


def zone_or_raise(name: str, *, where: str = "") -> tzinfo:
    normalized = str(name or "").strip()
    if normalized:
        try:
            return ZoneInfo(normalized)
        except Exception as failure:
            raise UnknownTimeZone(normalized, where=where) from failure
    raise UnknownTimeZone("", where=where)


def is_known_zone(name: str) -> bool:
    try:
        zone_or_raise(name)
    except Exception:
        return False
    return True


def _iana_from_link_target(target: str) -> str:
    parts = tuple(part for part in str(target or "").split("/") if part and part != ".")
    for marker in _ZONEINFO_DIRS:
        matches = [index for index, part in enumerate(parts) if part == marker]
        if matches:
            return "/".join(parts[matches[-1] + 1 :])
    return ""


class MachineZoneCandidates:
    def read(self) -> list[tuple[str, str]]:
        candidates = [("TZ", str(os.environ.get("TZ") or "").strip().lstrip(":"))]
        try:
            if LOCALTIME_LINK.is_symlink():
                targets = (os.readlink(LOCALTIME_LINK), str(LOCALTIME_LINK.resolve()))
                candidates.extend(
                    ("/etc/localtime", _iana_from_link_target(target))
                    for target in targets
                )
        except OSError:
            pass
        try:
            if TIMEZONE_FILE.is_file():
                candidates.append(
                    ("/etc/timezone", TIMEZONE_FILE.read_text(encoding="utf-8").strip())
                )
        except OSError:
            pass
        return candidates

    def resolve(self) -> str:
        for source, name in self.read():
            if not name:
                continue
            if is_known_zone(name):
                return name
            logger.debug(
                "machine timezone from %s is %r, which is not an IANA key", source, name
            )
        return ""


def machine_zone_name() -> str:
    return MachineZoneCandidates().resolve()


def _read_config_name() -> str:
    try:
        from gideon.core.config.loader import AppConfig

        return str(AppConfig.load().timezone or "").strip()
    except Exception:
        return ""


def _config_zone_name() -> str:
    configured = _read_config_name()
    if not configured or is_known_zone(configured):
        return configured
    logger.warning(
        "config.timezone is %r, which is not an IANA zone name — ignoring it and falling back to this machine's zone. Fix it with `gideon setup`.",
        configured,
    )
    return ""


def resolve_zone_name(explicit: str = "") -> tuple[str, str]:
    requested = str(explicit or "").strip()
    if requested:
        zone_or_raise(requested, where="timezone")
        return requested, SOURCE_EXPLICIT
    precedence = (
        (SOURCE_CONFIG, _config_zone_name),
        (SOURCE_MACHINE, machine_zone_name),
    )
    for source, read in precedence:
        if name := read():
            return name, source
    return UTC_NAME, SOURCE_UTC_FALLBACK


def resolve_zone(explicit: str = "") -> tzinfo:
    name, source = resolve_zone_name(explicit)
    if source != SOURCE_UTC_FALLBACK:
        try:
            return ZoneInfo(name)
        except Exception:
            logger.warning(
                "zone %r resolved but could not be constructed; using UTC", name
            )
    return timezone.utc


def local_utc_offset_hours() -> float:
    try:
        delta = datetime.now().astimezone().utcoffset()
        return round(delta.total_seconds() / 3600.0, 2) if delta else 0.0
    except Exception:
        return 0.0


def unresolved_zone_warning() -> str:
    if resolve_zone_name()[1] != SOURCE_UTC_FALLBACK:
        return ""
    offset = local_utc_offset_hours()
    consequence = (
        f"timed triggers will fire at UTC, which is {abs(offset):g} hour(s) off this host's local time (UTC{offset:+g})"
        if offset
        else "timed triggers will fire at UTC — the same offset as this host right now, but a UTC fallback does not follow daylight saving, so it will drift by an hour"
    )
    return (
        f"this machine's timezone could not be determined ({LOCALTIME_LINK} is not a "
        f"symlink to a zoneinfo entry, {TIMEZONE_FILE} is absent, and TZ is unset or "
        f"unusable), so {consequence}. Fix it with `gideon setup` or by setting "
        "`timezone` to an IANA name in config.json."
    )


def zone_report() -> dict[str, Any]:
    machine = machine_zone_name()
    configured = _read_config_name()
    acceptable = not configured or is_known_zone(configured)
    try:
        selected, source = resolve_zone_name()
    except Exception:
        selected, source = UTC_NAME, SOURCE_UTC_FALLBACK
    warning = unresolved_zone_warning()
    if not warning and not acceptable:
        warning = (
            f"config.timezone is {configured!r}, which is not an IANA zone name — it is "
            f"being ignored and schedules resolve to {selected!r} instead. Fix it with `gideon setup`."
        )
    return dict(
        resolved=selected,
        source=source,
        config=configured,
        config_ok=acceptable,
        machine=machine,
        utc_offset_hours=local_utc_offset_hours(),
        warning=warning,
    )

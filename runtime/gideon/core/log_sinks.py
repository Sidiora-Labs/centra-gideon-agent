"""Consistent, redacted gateway log sinks with loaded app attribution."""

import logging
import re

from gideon.extensions.apps.code_provenance import loaded_app
from gideon.security.security import redact_for_display

_level = logging.WARNING
_private_path = re.compile(r"(?<![\w:])/(?:root|home|Users|tmp|private)/[^\s\"'<>]+")


def sanitize(text: str) -> str:
    return _private_path.sub("[private path]", redact_for_display(text))


def level() -> int:
    return _level


def set_level(value: int) -> None:
    global _level
    _level = value
    logging.getLogger().setLevel(value)
    logging.getLogger("gideon").setLevel(logging.NOTSET)


def shown(record: logging.LogRecord) -> bool:
    product = record.name == "gideon" or record.name.startswith("gideon.")
    return record.levelno >= _level and (
        record.levelno >= logging.WARNING
        or product
        or loaded_app(record.pathname) is not None
    )


class _Shown(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not shown(record):
            return False
        record.msg = sanitize(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = sanitize(
                logging.Formatter().formatException(record.exc_info)
            )
        return True


_filter = _Shown()


def attach(handler: logging.Handler) -> None:
    handler.setLevel(logging.NOTSET)
    handler.addFilter(_filter)
    logging.getLogger().addHandler(handler)


def detach(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)

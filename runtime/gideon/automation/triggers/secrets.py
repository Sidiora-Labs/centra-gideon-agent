"""Resolve credential references into ephemeral action configuration."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)
SECRET_REF_RE = re.compile(r"\{\{\s*secret:([A-Za-z0-9_.\-]+)\s*\}\}")


class UnresolvedSecret(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        message = (
            f"the action references {{{{secret:{key}}}}}, which is not in the credential store — "
            "add it with `gideon auth` or remove the reference"
        )
        super().__init__(message)


def _strings(value: Any) -> Iterator[str]:
    pending = [iter((value,))]
    while pending:
        try:
            node = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            pending.append(iter(node.values()))
        elif isinstance(node, (list, tuple)):
            pending.append(iter(node))


def references(value: Any) -> list[str]:
    ordered = dict.fromkeys(
        match.group(1)
        for text in _strings(value)
        for match in SECRET_REF_RE.finditer(text)
    )
    return list(ordered)


@dataclass(frozen=True)
class CredentialSubstitution:
    values: dict[str, str]

    def text(self, value: str) -> str:
        complete = SECRET_REF_RE.fullmatch(value.strip())
        if complete is not None:
            return self.values[complete.group(1)]
        pieces: list[str] = []
        cursor = 0
        for reference in SECRET_REF_RE.finditer(value):
            pieces.extend(
                (value[cursor : reference.start()], self.values[reference.group(1)])
            )
            cursor = reference.end()
        pieces.append(value[cursor:])
        return "".join(pieces)

    def tree(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return dict(zip(value.keys(), map(self.tree, value.values())))
        if isinstance(value, list):
            return list(map(self.tree, value))
        if isinstance(value, tuple):
            return tuple(map(self.tree, value))
        return value


def default_resolver(key: str) -> str:
    from gideon.core.config.loader import config_dir
    from gideon.integrations.llm.credentials import CredentialStore

    try:
        credential = CredentialStore(config_dir()).resolve(key)
    except KeyError:
        return ""
    except Exception:
        logger.debug(
            "credential store unreadable while resolving %r", key, exc_info=True
        )
        return ""
    return credential.secret or ""


def resolve(config: Any, *, resolver: Callable[[str], str] | None = None) -> Any:
    requested = references(config)
    if not requested:
        return config
    lookup = resolver or default_resolver
    credentials = {}
    for name in requested:
        credentials[name] = lookup(name)
        if not credentials[name]:
            raise UnresolvedSecret(name)
    return CredentialSubstitution(credentials).tree(config)

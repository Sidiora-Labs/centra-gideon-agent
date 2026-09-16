"""Bounded session membership for temporary and incognito memory policies."""

from __future__ import annotations

from collections import OrderedDict

_MAX = 10_000


class _RestrictionRegistry:
    def __init__(self) -> None:
        self.temporary: OrderedDict[str, None] = OrderedDict()
        self.incognito: OrderedDict[str, None] = OrderedDict()

    def memberships(self, key: str) -> tuple[bool, bool]:
        return key in self.temporary, key in self.incognito

    def forget(self, key: str) -> None:
        for index in (self.temporary, self.incognito):
            index.pop(key, None)

    @staticmethod
    def retain(index: OrderedDict[str, None], key: str) -> None:
        index.pop(key, None)
        index[key] = None
        if len(index) > _MAX:
            del index[next(iter(index))]


_registry = _RestrictionRegistry()
_temporary = _registry.temporary
_incognito = _registry.incognito


def _add(store: OrderedDict[str, None], key: str) -> None:
    _registry.retain(store, key)


def mark_temporary(session_key: str) -> None:
    _registry.retain(_temporary, session_key)


def mark_incognito(session_key: str) -> None:
    _registry.retain(_incognito, session_key)


def is_temporary(session_key: str) -> bool:
    return _registry.memberships(session_key)[0]


def is_incognito(session_key: str) -> bool:
    return _registry.memberships(session_key)[1]


def is_restricted(session_key: str) -> bool:
    return any(_registry.memberships(session_key))


def clear(session_key: str) -> None:
    _registry.forget(session_key)

"""Registered event origins, browsable vocabularies and attributed ingestion."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gideon.automation.trigger_sources.base import (
        SourceEvent,
        TriggerSourceProvider,
    )

logger = logging.getLogger(__name__)
NAMESPACE_PREFIX = "app"


class SourceDirectory:
    def __init__(self):
        self.providers: dict[str, TriggerSourceProvider] = {}
        self.unlisted: dict[str, set[str]] = {}

    def attach(self, provider: TriggerSourceProvider) -> None:
        self.providers.update({provider.name: provider})

    def detach(self, name: str) -> None:
        for mapping in (self.providers, self.unlisted):
            mapping.pop(name, None)

    def declarations(self) -> dict[str, tuple[str, ...]]:
        result = {}
        for name, provider in self.providers.items():
            try:
                result[name] = tuple(
                    str(event) for event in provider.events if str(event)
                )
            except Exception:
                logger.debug(
                    "trigger source %r could not list its events", name, exc_info=True
                )
                result[name] = ()
        return result

    def note(self, source: str, event: str) -> None:
        try:
            provider = self.providers.get(source)
            declared = set(map(str, provider.events if provider else ()))
            if event not in declared:
                self.unlisted.setdefault(source, set()).add(event)
        except Exception:
            logger.debug("could not check %r's declared events", source, exc_info=True)

    def gaps(self, name: str) -> dict[str, list[str]]:
        if name:
            return {name: sorted(self.unlisted[name])} if name in self.unlisted else {}
        return {
            source: sorted(events) for source, events in self.unlisted.items() if events
        }


_directory = SourceDirectory()


def namespace(source_name: str, event: str) -> str:
    return ":".join(map(str, (NAMESPACE_PREFIX, source_name, event)))


def register_source(provider: TriggerSourceProvider) -> None:
    _directory.attach(provider)


def unregister_source(name: str) -> None:
    _directory.detach(name)


def get_source(name: str) -> TriggerSourceProvider | None:
    return _directory.providers.get(name)


def list_sources() -> list[str]:
    return [*_directory.providers]


def declared_events() -> dict[str, tuple[str, ...]]:
    return _directory.declarations()


def namespaced_events() -> list[str]:
    catalog = declared_events()
    names = [
        namespace(source, event) for source in catalog for event in catalog[source]
    ]
    names.sort()
    return names


def undeclared_events(name: str = "") -> dict[str, list[str]]:
    return _directory.gaps(name)


def _note_undeclared(source_name: str, event_name: str) -> None:
    _directory.note(source_name, event_name)


def _provenance_meta(
    source_name: str, event_name: str, namespaced: str, event: SourceEvent
) -> dict[str, Any]:
    supplied = getattr(event, "meta", None)
    metadata = {
        str(key): value if isinstance(value, (str, int, float, bool)) else str(value)
        for key, value in (supplied.items() if isinstance(supplied, dict) else ())
    }
    authoritative = dict(
        app=source_name,
        app_event=event_name,
        source_event=namespaced,
        provenance=f"{NAMESPACE_PREFIX}:{source_name}",
    )
    return metadata | authoritative


@dataclass(frozen=True)
class IngestedEvent:
    source: str
    name: str
    key: str
    text: str
    original: SourceEvent

    @classmethod
    def read(cls, source: str, event: SourceEvent) -> IngestedEvent:
        attributes = {
            name: str(getattr(event, name, "") or "")
            for name in ("event", "key", "text")
        }
        return cls(
            source,
            attributes["event"].strip(),
            attributes["key"],
            attributes["text"],
            event,
        )

    def publish(self, now: float) -> str:
        from gideon.automation.event_triggers import SOURCE_APP, emit_event
        from gideon.security.security import fence_untrusted

        identity = namespace(self.source, self.name)
        framed = fence_untrusted(
            self.text,
            source=f"trigger:{identity}",
            source_type=f"{NAMESPACE_PREFIX}:{self.source}",
            source_id=self.key or self.name,
            transformation_path="app-source:emit",
        )
        emit_event(
            source=SOURCE_APP,
            event_type=identity,
            key=self.key,
            value=framed,
            now=now or time.time(),
            meta=_provenance_meta(self.source, self.name, identity, self.original),
        )
        return identity


def emit(source_name: str, event: SourceEvent, *, now: float = 0.0) -> str:
    try:
        if source_name not in _directory.providers:
            logger.debug(
                "dropping an event from unregistered trigger source %r", source_name
            )
            return ""
        incoming = IngestedEvent.read(source_name, event)
        if incoming.name:
            _note_undeclared(source_name, incoming.name)
            return incoming.publish(now)
        logger.warning(
            "trigger source %r emitted an event with no name; dropped (an unnamed event matches no authorable glob)",
            source_name,
        )
    except Exception:
        logger.debug(
            "app trigger-source ingestion failed for %r", source_name, exc_info=True
        )
    return ""

"""Select owner-attributed automation rows before arming or firing them."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)
FOREIGN_AUTHOR = "foreign_author"


def owner_username() -> str:
    try:
        from gideon.cognition.identity import current_username

        name = current_username()
    except Exception:
        logger.debug(
            "triggers.ownership: username unreadable — treating every row as the owner's"
        )
        name = ""
    return name


@dataclass(frozen=True)
class OwnerSelection:
    username: str

    @classmethod
    def resolve(cls, owner: str | None) -> OwnerSelection:
        return cls((owner_username() if owner is None else owner).strip().lower())

    def accepts(self, trigger: Any) -> bool:
        attributed = str(getattr(trigger, "author", "") or "").strip().lower()
        return not (self.username and attributed and self.username != attributed)


def is_owner_authored(trigger: Any, *, owner: str | None = None) -> bool:
    return OwnerSelection.resolve(owner).accepts(trigger)


def owner_authored(triggers: Iterable[Any], *, owner: str | None = None) -> list[Any]:
    selection = OwnerSelection.resolve(owner)
    return list(filter(selection.accepts, triggers))

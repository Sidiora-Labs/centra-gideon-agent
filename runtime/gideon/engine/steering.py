"""String-compatible steering with server provenance and a consumption receipt."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable


class SteeringText(str):
    meta: dict
    on_consumed: Callable[[SteeringText], None] | None
    consumed: bool

    def __new__(cls, text: str, *, meta: dict | None = None):
        value = super().__new__(cls, text)
        value.meta = deepcopy(meta or {})
        value.on_consumed = None
        value.consumed = False
        return value

    def acknowledge(self) -> None:
        if not self.consumed:
            if self.on_consumed is not None:
                self.on_consumed(self)
            self.consumed = True


def acknowledge_steering(text: str) -> None:
    if isinstance(text, SteeringText):
        text.acknowledge()

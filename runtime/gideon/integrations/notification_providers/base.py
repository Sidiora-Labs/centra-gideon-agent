from abc import ABC, abstractmethod
from typing import Any


class NotificationDeliveryProvider(ABC):
    @property
    @abstractmethod
    def delivery_name(self) -> str: ...

    @abstractmethod
    def can_reach(self, addressee: str) -> bool: ...

    @abstractmethod
    def deliver(self, notification: dict[str, Any]) -> None: ...

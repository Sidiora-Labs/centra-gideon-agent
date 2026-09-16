"""SDK: the memory-provider ABC + data types.

Stable re-export of ``gideon.integrations.memory_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from gideon.integrations.memory_providers.base import MemoryProvider

__all__ = ["MemoryProvider"]

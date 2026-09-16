"""SDK: the message-source (inbox) provider ABC + data types.

Stable re-export of ``gideon.integrations.inbox_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from gideon.integrations.inbox_providers.base import (
    IncomingMessage,
    MessageSourceProvider,
)

__all__ = ["MessageSourceProvider", "IncomingMessage"]

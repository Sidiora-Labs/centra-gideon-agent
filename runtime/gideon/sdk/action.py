"""SDK: the action (trigger) provider ABC + data types.

Stable re-export of ``gideon.action_providers.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.
"""

from gideon.action_providers.base import (  # noqa: F401
    ActionContext,
    ActionProvider,
    ActionResult,
)

__all__ = ["ActionProvider", "ActionContext", "ActionResult"]

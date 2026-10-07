"""Owner-attributed inbox projections with recursive display redaction."""

from gideon.integrations.inbox import redact_item
from gideon.security.security import redact_for_display


def owner_item(item, owner: str) -> dict:
    return mask_inbox_projection(redact_item(item.to_owner_dict(owner)))


_OPAQUE_INBOX_FIELDS = frozenset(
    {
        "id",
        "channel",
        "thread_ts",
        "sender_id",
        "reply_target",
        "source",
        "status",
        "owner",
        "created_at",
    }
)


def mask_inbox_projection(value):
    if isinstance(value, dict):
        return {
            key: (
                item
                if key in _OPAQUE_INBOX_FIELDS
                or key.endswith("_id")
                or key.endswith("_ts")
                else mask_inbox_projection(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_inbox_projection(item) for item in value]
    if isinstance(value, str):
        return redact_for_display(value)
    return value

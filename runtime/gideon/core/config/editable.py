"""Editable configuration fields shared with the dashboard config editor."""

EDITABLE_CONFIG_FIELDS: dict[str, dict[str, object]] = {
    "inbox.sort_messages": {"type": "bool"},
    "security.outside_home": {"type": "str_list", "max_items": 20},
}

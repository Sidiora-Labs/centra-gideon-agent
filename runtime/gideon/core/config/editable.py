"""Editable configuration fields shared with the dashboard config editor."""

EDITABLE_CONFIG_FIELDS: dict[str, dict[str, object]] = {
    "security.outside_home": {"type": "str_list", "max_items": 20},
}

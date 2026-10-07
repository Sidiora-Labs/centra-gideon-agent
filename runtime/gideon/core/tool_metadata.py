"""Parse and redact tool message metadata for every conversation surface."""

import json

from gideon.security.security import redact_credentials, redact_exfiltration_urls


def parse_cls_meta(cls_val: str) -> dict | None:
    """Parse a JSON-encoded ``cls`` string into a meta dict.

    Returns the parsed dict (with ``tool_input`` sanitized) or ``None``
    if ``cls_val`` is not valid JSON or not a dict.  Used by both
    ``_prepare_messages`` (HTTP history) and ``_broadcast_chat_message``
    (live WS push) so the frontend sees an identical ``meta`` structure.
    """
    if not cls_val:
        return None
    try:
        meta = json.loads(cls_val)
        if not isinstance(meta, dict):
            return None
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(meta.get("tool_input"), str):
        sanitized, _ = redact_exfiltration_urls(meta["tool_input"])
        sanitized, _ = redact_credentials(sanitized)
        meta["tool_input"] = sanitized

    if "request_id" in meta and "approval_id" not in meta:
        meta["approval_id"] = meta.pop("request_id")

    return meta

"""Shared constants used across cli and gateway modules."""

DATA_WARNING = (
    "⚠️  Do not share confidential, sensitive, or regulated data with AI models.\n"
    "   Review your organization's AI usage and data handling policies\n"
    "   before entering sensitive information."
)

CHAT_TURN_TIMEOUT = 600.0

JSONRPC_METHOD_NOT_FOUND = -32601

DASHBOARD_SESSION_PREFIX = "dashboard:"


def dashboard_session_key(session_name: str) -> str:
    prefix = (
        ""
        if session_name.startswith(DASHBOARD_SESSION_PREFIX)
        else DASHBOARD_SESSION_PREFIX
    )
    return "".join((prefix, session_name))

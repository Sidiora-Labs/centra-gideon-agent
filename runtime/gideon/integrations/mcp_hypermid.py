"""Lifecycle composition between Gideon sessions and Hypermid modules."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def evict_hypermid_session(session_key: str) -> None:
    """Stop only module and daemon-MCP children owned by the ending Gideon session."""
    try:
        from gideon.hypermid.modules import close_session_modules
        from gideon.hypermid.mcp_provider import evict_daemon_mcp_session

        results = await asyncio.gather(
            close_session_modules(session_key),
            evict_daemon_mcp_session(session_key),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError("Hypermid session eviction was incomplete") from failures[0]
    except Exception:
        logger.debug(
            "Hypermid module eviction failed for ending session",
            exc_info=True,
        )


__all__ = ["evict_hypermid_session"]

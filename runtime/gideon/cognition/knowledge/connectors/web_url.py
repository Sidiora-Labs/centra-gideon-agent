"""Web URL remote source connector — fetches any publicly accessible page."""

import hashlib
import logging

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

from gideon.cognition.knowledge.connectors.base import (
    BaseConnector,
    extract_html_metadata,
)
from gideon.integrations.web.extract import extract_main_content

logger = logging.getLogger(__name__)


def _friendly_fetch_error(exc: Exception) -> tuple[str, str]:
    """Map a raw fetch exception to (human-readable reason, kind) for the bookmark's
    processing_error — so the UI shows 'Couldn't reach the site' instead of a bare
    '[Errno 8] nodename nor servname provided'.

    kind is ``"unreachable"`` for environmental/network problems the user can simply
    retry (DNS/connect/timeout/refused/5xx/429) — these are NOT an unexpected processing
    failure, so the pipeline marks the item ``unreachable`` (retryable) rather than
    ``failed``. kind is ``"error"`` for anything else (a real, unexpected fetch fault).
    """
    if _httpx is not None:
        status_err = getattr(_httpx, "HTTPStatusError", None)
        if status_err and isinstance(exc, status_err):
            code = getattr(getattr(exc, "response", None), "status_code", None)
            msg = (
                f"The site returned HTTP {code}."
                if code
                else "The site returned an error response."
            )
            return msg, "unreachable"
        timeout_err = getattr(_httpx, "TimeoutException", None)
        if timeout_err and isinstance(exc, timeout_err):
            return "The site took too long to respond (timed out).", "unreachable"
        connect_err = getattr(_httpx, "ConnectError", None)
        if connect_err and isinstance(exc, connect_err):
            return (
                "Couldn't reach the site (it may not exist or is unreachable).",
                "unreachable",
            )
    msg = str(exc).lower()
    if (
        "nodename nor servname" in msg
        or "name or service not known" in msg
        or "getaddrinfo" in msg
    ):
        return (
            "Couldn't reach the site (the address could not be resolved).",
            "unreachable",
        )
    if "timed out" in msg or "timeout" in msg:
        return "The site took too long to respond (timed out).", "unreachable"
    if "connection refused" in msg or "refused" in msg:
        return "The site refused the connection.", "unreachable"
    return f"Couldn't fetch the page: {exc}", "error"


class WebUrlConnector(BaseConnector):
    """Connector that fetches and stores text content from any web URL."""

    _HEADERS = {"User-Agent": "Gideon-KnowledgeBot/1.0"}  # noqa: E501

    def source_type(self) -> str:
        return "web_url"

    def validate_config(self, config: dict) -> tuple[bool, str]:
        url = (config.get("uri") or config.get("url") or "").strip()
        if not url:
            return False, "URL is required"
        if not url.startswith(("http://", "https://")):
            return False, "URL must start with http:// or https://"
        return True, ""

    async def fetch(self, source: dict) -> tuple[str, dict]:
        url = (source.get("uri") or source.get("url") or "").strip()
        if not url:
            return "", {"error": "No URL configured"}
        from gideon.security.net import CONNECTOR, EgressBlocked, egress_policy_for
        from gideon.security.net import fetch as net_fetch

        try:
            resp = await net_fetch(
                url, policy=egress_policy_for(CONNECTOR), headers=self._HEADERS
            )
            if resp.status >= 400:
                return "", {
                    "error": f"The site returned HTTP {resp.status}.",
                    "error_kind": "unreachable",
                    "url": url,
                }
            content_type = resp.headers.get("Content-Type", "") or resp.headers.get(
                "content-type", ""
            )
            raw = resp.text
            page_meta: dict = {}
            if "html" in content_type:
                text = extract_main_content(raw, url=resp.url).text
                page_meta = extract_html_metadata(raw)
            else:
                text = raw
            content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
            meta = {
                "url": resp.url,
                "etag": resp.headers.get("ETag", "") or resp.headers.get("etag", ""),
                "last_modified": resp.headers.get("Last-Modified", "")
                or resp.headers.get("last-modified", ""),
                "content_hash": content_hash,
                "content_type": content_type,
                "page_title": page_meta.get("title", ""),
                "page_description": page_meta.get("description", ""),
            }
            return text, meta
        except EgressBlocked as e:
            logger.warning("WebUrl fetch blocked by egress guard for %s: %s", url, e)
            return "", {
                "error": f"Blocked by the network security guard: {e}",
                "error_kind": "blocked",
                "url": url,
            }
        except Exception as e:
            logger.error("WebUrl fetch failed for %s: %s", url, e)
            reason, kind = _friendly_fetch_error(e)
            return "", {"error": reason, "error_kind": kind, "url": url}

    async def detect_changes(self, source: dict) -> bool:
        url = (source.get("uri") or source.get("url") or "").strip()
        if not url:
            return False
        stored_meta = source.get("metadata") or {}
        from gideon.security.net import CONNECTOR, egress_policy_for
        from gideon.security.net import fetch as net_fetch

        try:
            r = await net_fetch(url, policy=egress_policy_for(CONNECTOR), method="HEAD")
            etag = r.headers.get("ETag", "") or r.headers.get("etag", "")
            last_modified = r.headers.get("Last-Modified", "") or r.headers.get(
                "last-modified", ""
            )
            if etag and etag != stored_meta.get("etag"):
                return True
            if last_modified and last_modified != stored_meta.get("last_modified"):
                return True
            if not etag and not last_modified:
                return True
            return False
        except Exception as e:
            logger.error("WebUrl detect_changes failed for %s: %s", url, e)
            return False

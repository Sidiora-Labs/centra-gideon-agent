"""Web fetch for knowledge ingestion.

``WebUrlConnector`` fetches any public web page → markdown text; it is used by the
bookmark node-graph (``BookmarkScrapeNode``) to scrape a bookmark's URL at ingest.
``BaseConnector`` is the minimal interface it implements.
"""

from gideon.cognition.knowledge.connectors.base import BaseConnector
from gideon.cognition.knowledge.connectors.web_url import WebUrlConnector

__all__ = ["BaseConnector", "WebUrlConnector"]

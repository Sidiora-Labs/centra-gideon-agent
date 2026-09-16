"""Bounded insight extraction with fenced inputs and normalized, redacted output."""

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gideon.security.security import redact_credentials, redact_exfiltration_urls

if TYPE_CHECKING:
    from gideon.cognition.knowledge.llm_pool import LLMPool

INSIGHTS_TIMEOUT = 180.0
_LIST_KEYS = ("key_points", "topics", "action_items")


def _insights_prompt(content: str) -> str:
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt
    from gideon.security.security import fence_untrusted

    variables = {"content": fence_untrusted(content, source="ingested")}
    return render_use_case_prompt("knowledge_insights", variables) or ""


def _redact(text: str | None) -> str:
    if text:
        for scrub in (redact_exfiltration_urls, redact_credentials):
            text, _ = scrub(text)
    return text or ""


@dataclass(frozen=True)
class _InsightDocument:
    payload: dict

    def normalized(self) -> dict:
        fields = {}
        title = _redact(str(self.payload.get("title") or "")).strip().rstrip(".")
        summary = _redact(str(self.payload.get("summary") or "")).strip()
        if title:
            fields["title"] = title[:120]
        if summary:
            fields["summary"] = summary
        for category in _LIST_KEYS:
            raw = self.payload.get(category)
            if isinstance(raw, list):
                cleaned = list(
                    filter(None, (_redact(str(entry)).strip() for entry in raw))
                )
                if cleaned:
                    fields[category] = cleaned[:6]
        return fields


def _response_documents(response: str):
    yield response
    yield _code_block(response)
    match = re.search(r"\{[\s\S]*\}", response or "")
    if match:
        yield match.group()


class InsightsExtractor:
    def __init__(self, pool: "LLMPool | None" = None, *, max_chars: int = 12000):
        self._pool = pool
        self._max_chars = max_chars

    async def extract(self, content: str, *, raise_on_error: bool = False) -> dict:
        if self._pool and (content or "").strip():
            try:
                response = await self._pool.send(
                    _insights_prompt(content[: self._max_chars]),
                    timeout=INSIGHTS_TIMEOUT,
                )
                return self._parse(response)
            except Exception:
                if raise_on_error:
                    raise
        return {}

    def _parse(self, response: str) -> dict:
        payload = self._loads(response)
        return (
            _InsightDocument(payload).normalized() if isinstance(payload, dict) else {}
        )

    @staticmethod
    def _loads(response: str) -> object:
        for candidate in _response_documents(response):
            if candidate:
                try:
                    decoded = json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    continue
                return decoded
        return None


def _code_block(response: str) -> str | None:
    blocks = re.finditer(r"```(?:json)?\s*\n?([\s\S]*?)```", response or "")
    block = next(blocks, None)
    return block.group(1).strip() if block is not None else None

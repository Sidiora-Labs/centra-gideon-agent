"""Structured graph extraction with bounded prompts and tolerant JSON decoding."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from gideon.cognition.knowledge.llm_pool import WorkerError

if TYPE_CHECKING:
    from gideon.cognition.knowledge.llm_pool import LLMPool

EXTRACTION_TIMEOUT = 180.0
_MAX_CHARS = 12_000


def _extraction_prompt(chunk: str) -> str:
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    return render_use_case_prompt("knowledge_extraction", {"chunk": chunk}) or ""


def _empty_result() -> dict:
    return dict(title="", entities=[], relations=[], category="document", summary="")


class EntityExtractor:
    def __init__(self, pool: "LLMPool | None" = None):
        self._pool = pool

    async def extract(self, chunk: str) -> dict:
        if not self._pool or not chunk.strip():
            return _empty_result()
        try:
            response = await self._pool.send(
                _extraction_prompt(chunk[:_MAX_CHARS]), timeout=EXTRACTION_TIMEOUT
            )
            return self._parse_response(response)
        except WorkerError:
            raise
        except Exception:
            return _empty_result()

    async def extract_batch(self, chunks: list[str]) -> list[dict]:
        results = [_empty_result() for _ in chunks]
        if not self._pool or not chunks:
            return results
        requests = [
            (index, _extraction_prompt(chunk[:_MAX_CHARS]))
            for index, chunk in enumerate(chunks)
            if chunk.strip()
        ]
        try:
            responses = await self._pool.send_batch(
                [prompt for _, prompt in requests], timeout=EXTRACTION_TIMEOUT
            )
            for (index, _), response in zip(requests, responses):
                results[index] = self._parse_response(response)
        except Exception:
            return [_empty_result() for _ in chunks]
        return results

    def _parse_response(self, response: str) -> dict:
        candidates = [response, self._extract_code_block(response)]
        match = re.search(r"\{[\s\S]*\}", response)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return self._validate(json.loads(candidate))
            except (json.JSONDecodeError, ValueError):
                continue
        return _empty_result()

    @staticmethod
    def _extract_code_block(text: str) -> str | None:
        match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        return match.group(1).strip() if match else None

    @staticmethod
    def _normalize_entities(entities) -> list[dict]:
        accepted = []
        for value in entities if isinstance(entities, list) else []:
            if isinstance(value, str):
                if value.strip():
                    accepted.append({"name": value.strip()})
            elif isinstance(value, dict) and str(value.get("name") or "").strip():
                accepted.append(value)
        return accepted

    @staticmethod
    def _normalize_relations(relations) -> list[dict]:
        return (
            [
                value
                for value in relations
                if isinstance(value, dict)
                and all(
                    str(value.get(key) or "").strip() for key in ("source", "target")
                )
            ]
            if isinstance(relations, list)
            else []
        )

    @classmethod
    def _validate(cls, data: dict) -> dict:
        normalizers = {
            "entities": cls._normalize_entities,
            "relations": cls._normalize_relations,
        }
        result = _empty_result()
        for field, default in result.items():
            value = data.get(field, default)
            result[field] = normalizers[field](value) if field in normalizers else value
        return result

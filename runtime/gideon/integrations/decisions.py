"""Bounded Jev choice validation and decision transport."""

from __future__ import annotations

import json
import math
import os
from typing import Any

import aiohttp

MODEL = "typesafe/jev-1.13"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"


def validate_choice(answer: Any, options: dict[str, Any]) -> str:
    try:
        probabilities = answer["probabilities"]
        values = [*probabilities.values(), answer["confidence"]]
        chosen = answer["choice"]
        valid = (
            isinstance(chosen, str)
            and chosen in options
            and set(probabilities) == set(options)
            and all(
                type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1
                for v in values
            )
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[chosen] >= max(probabilities.values())
        )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        valid = False
    if not valid:
        raise ValueError("Invalid Jev choice; no decision accepted")
    return chosen


def _service_name(purpose: str) -> str:
    if purpose not in ("browser", "genui"):
        raise ValueError("Unsupported decision purpose")
    return "Browser" if purpose == "browser" else "GenUI"


def decision_target(
    body: dict,
    *,
    purpose: str = "genui",
    api_key: str = "",
) -> tuple[str, str, dict]:
    _service_name(purpose)
    if not api_key:
        raise RuntimeError(f"OpenRouter {purpose} decisions are not configured")
    return ENDPOINT, api_key, {**body, "model": MODEL}


async def request_decision(body: dict, *, purpose: str = "genui") -> dict:
    service = _service_name(purpose)
    url, key, payload = decision_target(
        body,
        purpose=purpose,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as client:
        async with client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {key}", "X-Title": "Gideon"},
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"{service} decision service rejected the request ({response.status})"
                )
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise RuntimeError(
                        f"{service} decision response exceeded its limit"
                    )
                chunks.append(chunk)
            result = json.loads(b"".join(chunks))
            if not isinstance(result, dict):
                raise ValueError(f"Invalid {service} decision response")
            return result

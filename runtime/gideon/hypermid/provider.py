from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class ProviderSerializationViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def profile_digest(capabilities: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(capabilities))).hexdigest()


@dataclass(frozen=True, slots=True)
class SerializedProjection:
    blocks: tuple[Mapping[str, Any], ...]
    model_budget: Mapping[str, Any]
    serialized_digest: str
    generation: int
    provider_profile_digest: str
    render_mode: str = "host_serialized"


def serialize_for_host(
    projection: Mapping[str, Any],
    profile: Mapping[str, Any],
    previous_generation: Mapping[str, Any] | None = None,
) -> SerializedProjection:
    capabilities = {key: value for key, value in profile.items() if key != "profile_digest"}
    digest = profile_digest(capabilities)
    if profile.get("profile_digest") != digest:
        raise ProviderSerializationViolation("PROFILE_DIGEST_MISMATCH", "profile digest is invalid")
    if projection.get("provider_profile_digest") != digest:
        raise ProviderSerializationViolation("PROJECTION_PROFILE_MISMATCH", "projection profile differs")
    generation = projection.get("generation")
    if not isinstance(generation, int) or generation < 1:
        raise ProviderSerializationViolation("INVALID_GENERATION", "generation must be positive")
    if previous_generation is not None:
        previous_digest = previous_generation.get("profile_digest")
        previous_number = previous_generation.get("generation")
        if previous_digest != digest and (
            not isinstance(previous_number, int) or generation <= previous_number
        ):
            raise ProviderSerializationViolation("GENERATION_NOT_ADVANCED", "profile change requires a new generation")

    roles = frozenset(profile.get("roles", ()))
    kinds = frozenset(profile.get("part_kinds", ()))
    adjacency = profile.get("requires_tool_adjacency") is True
    pending: list[str] = []
    images = 0
    cache_boundaries = 0
    raw_blocks = projection.get("blocks")
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
        raise ProviderSerializationViolation("INVALID_BLOCKS", "normalized blocks are required")
    blocks: list[Mapping[str, Any]] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, Mapping) or raw_block.get("role") not in roles:
            raise ProviderSerializationViolation("UNSUPPORTED_ROLE", "provider does not support block role")
        if raw_block.get("cache_boundary") == "after":
            cache_boundaries += 1
            if profile.get("supports_cache_boundaries") is not True:
                raise ProviderSerializationViolation("UNSUPPORTED_CACHE_BOUNDARY", "cache boundary is unsupported")
        raw_parts = raw_block.get("parts")
        if not isinstance(raw_parts, Sequence) or isinstance(raw_parts, (str, bytes)):
            raise ProviderSerializationViolation("INVALID_PARTS", "block parts are required")
        for part in raw_parts:
            if not isinstance(part, Mapping) or part.get("kind") not in kinds:
                raise ProviderSerializationViolation("UNSUPPORTED_PART", "provider does not support part kind")
            kind = part.get("kind")
            if kind == "reasoning" and profile.get("supports_reasoning") is not True:
                raise ProviderSerializationViolation("UNSUPPORTED_REASONING", "reasoning is unsupported")
            if kind in {"image", "file"}:
                images += 1
            if kind == "tool_call":
                if adjacency and pending:
                    raise ProviderSerializationViolation("TOOL_PAIR_NOT_ADJACENT", "tool result must immediately follow call")
                call_id = part.get("call_id")
                if not isinstance(call_id, str):
                    raise ProviderSerializationViolation("UNRESOLVED_TOOL_CALL", "tool call identity is required")
                pending.append(call_id)
            elif kind == "tool_result":
                if not pending:
                    raise ProviderSerializationViolation("ORPHAN_TOOL_RESULT", "tool result has no call")
                if pending.pop(0) != part.get("call_id"):
                    raise ProviderSerializationViolation("TOOL_PAIR_ORDER", "tool result order differs from calls")
            elif adjacency and pending:
                raise ProviderSerializationViolation("TOOL_PAIR_NOT_ADJACENT", "tool result must immediately follow call")
        blocks.append(dict(raw_block))
    if pending:
        raise ProviderSerializationViolation("UNRESOLVED_TOOL_CALL", "tool call has no result")
    if cache_boundaries > profile.get("max_cache_boundaries", 0):
        raise ProviderSerializationViolation("TOO_MANY_CACHE_BOUNDARIES", "cache boundary limit exceeded")
    if images > profile.get("max_images", 0):
        raise ProviderSerializationViolation("TOO_MANY_IMAGES", "image limit exceeded")

    window = profile.get("context_window_tokens")
    output = profile.get("reserved_output_tokens")
    if not isinstance(window, int) or not isinstance(output, int) or window < 1 or output < 0 or output > window:
        raise ProviderSerializationViolation("INVALID_PROFILE", "provider limits are invalid")
    budget_input = projection.get("budget_inputs")
    if not isinstance(budget_input, Mapping):
        raise ProviderSerializationViolation("INVALID_BUDGET", "projection budget is missing")
    region_masses = [projection.get(kind, {}).get("token_mass") for kind in ("baseline", "delta", "tail")]
    if any(not isinstance(value, int) or value < 0 for value in region_masses):
        raise ProviderSerializationViolation("INVALID_BUDGET", "region mass is invalid")
    used = sum(region_masses)
    safe_input = window - output
    projection_limit = budget_input.get("max_input_tokens")
    if not isinstance(projection_limit, int) or used > safe_input or used > projection_limit:
        raise ProviderSerializationViolation("BUDGET_EXCEEDED", "projection exceeds safe input budget")
    model_budget = {
        "context_window_tokens": window,
        "reserved_output_tokens": output,
        "max_input_tokens": min(safe_input, projection_limit),
        "max_items": budget_input.get("max_items"),
        "max_images": min(profile.get("max_images", 0), budget_input.get("max_images", 0)),
        "baseline_tokens": region_masses[0],
        "delta_tokens": region_masses[1],
        "tail_tokens": region_masses[2],
        "confidence": "conservative",
    }
    digest_input = {
        "blocks": blocks,
        "model_budget": model_budget,
        "provider_profile_digest": digest,
        "render_mode": "host_serialized",
    }
    return SerializedProjection(
        blocks=tuple(blocks),
        model_budget=model_budget,
        serialized_digest=hashlib.sha256(_canonical(digest_input)).hexdigest(),
        generation=generation,
        provider_profile_digest=digest,
    )

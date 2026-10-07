from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .protocol import Cursor, Scope


class ProjectionViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProjectionViolation("INVALID_PROJECTION", str(exc)) from exc


def _digest_sequence(values: Sequence[bytes]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _part_payload(part: Mapping[str, Any]) -> bytes:
    kind = part.get("kind")
    if kind in {"text", "reasoning", "context_marker"}:
        value = part.get("text")
    elif kind == "tool_call":
        if not part.get("call_id") or not part.get("tool_name"):
            raise ProjectionViolation("INVALID_PART", "tool call identity is required")
        value = part.get("arguments_json")
    elif kind == "tool_result":
        if not part.get("call_id"):
            raise ProjectionViolation("INVALID_PART", "tool result call id is required")
        value = part.get("result_json")
    elif kind in {"image", "file"}:
        if not part.get("media_type"):
            raise ProjectionViolation("INVALID_PART", "media type is required")
        value = part.get("source_uri")
    else:
        raise ProjectionViolation("INVALID_PART", "unsupported part kind")
    if not isinstance(value, str):
        raise ProjectionViolation("INVALID_PART", "part payload is required")
    return value.encode("utf-8")


def _validate_part(part: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(part)
    payload = _part_payload(normalized)
    expected = hashlib.sha256(payload).hexdigest()
    if normalized.get("content_digest") != expected:
        raise ProjectionViolation(
            "DIGEST_MISMATCH", "part content digest does not match"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    value: Mapping[str, Any]
    serialized: bytes
    region_bytes: Mapping[str, bytes]


def project(request: Mapping[str, Any]) -> ProjectionResult:
    raw_items = request.get("items")
    if (
        not isinstance(raw_items, Sequence)
        or isinstance(raw_items, (str, bytes))
        or not raw_items
    ):
        raise ProjectionViolation(
            "EMPTY_PROJECTION", "projection requires source items"
        )
    source_cursor = Cursor.from_wire(request.get("source_cursor"))
    budget = request.get("budget_inputs")
    if not isinstance(budget, Mapping):
        raise ProjectionViolation("INVALID_BUDGET", "budget inputs are required")
    max_items = budget.get("max_items")
    max_input = budget.get("max_input_tokens")
    if not isinstance(max_items, int) or len(raw_items) > max_items:
        raise ProjectionViolation("BUDGET_EXCEEDED", "item limit exceeded")

    seen: set[str] = set()
    previous: Cursor | None = None
    normalized_items: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    total_mass = 0
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ProjectionViolation("INVALID_ITEM", "source item must be an object")
        item = dict(raw_item)
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise ProjectionViolation(
                "DUPLICATE_ITEM", "source identities must be unique"
            )
        seen.add(item_id)
        cursor = Cursor.from_wire(item.get("cursor"))
        if (
            cursor.epoch != source_cursor.epoch
            or cursor > source_cursor
            or (previous and cursor <= previous)
        ):
            raise ProjectionViolation(
                "INVALID_CURSOR_ORDER", "items must cover an ordered committed cursor"
            )
        previous = cursor
        parts = item.get("parts")
        if (
            not isinstance(parts, Sequence)
            or isinstance(parts, (str, bytes))
            or not parts
        ):
            raise ProjectionViolation("EMPTY_PARTS", "source items require parts")
        normalized_parts = [
            _validate_part(part) for part in parts if isinstance(part, Mapping)
        ]
        if len(normalized_parts) != len(parts):
            raise ProjectionViolation("INVALID_PART", "part must be an object")
        item["parts"] = normalized_parts
        token_mass = item.get("token_mass")
        if not isinstance(token_mass, int) or token_mass < 0:
            raise ProjectionViolation("INVALID_MASS", "token mass must be non-negative")
        total_mass += token_mass
        normalized_items.append(item)
        part_digests = [
            bytes.fromhex(part["content_digest"]) for part in normalized_parts
        ]
        blocks.append(
            {
                "block_id": f"block:{item_id}",
                "role": item.get("role"),
                "parts": normalized_parts,
                "source_item_ids": [item_id],
                "content_digest": _digest_sequence(part_digests),
                "cache_boundary": "none",
                "synthetic": False,
            }
        )
    if not isinstance(max_input, int) or total_mass > max_input:
        raise ProjectionViolation("BUDGET_EXCEEDED", "input budget exceeded")

    regions: dict[str, dict[str, Any]] = {}
    region_bytes_by_kind: dict[str, bytes] = {}
    for kind in ("baseline", "delta", "tail"):
        selected = [
            (item, block)
            for item, block in zip(normalized_items, blocks, strict=True)
            if item.get("region") == kind
        ]
        region_blocks = [block for _, block in selected]
        region_bytes = _canonical(region_blocks)
        region_bytes_by_kind[kind] = region_bytes
        regions[kind] = {
            "kind": kind,
            "digest": _digest_sequence([_canonical(block) for block in region_blocks]),
            "item_ids": [item["item_id"] for item, _ in selected],
            "summary_ids": [],
            "token_mass": sum(item["token_mass"] for item, _ in selected),
        }

    output_digest = hashlib.sha256(_canonical(blocks)).hexdigest()
    source_digest = request.get("source_digest")
    if (
        not isinstance(source_digest, str)
        or len(source_digest) != 64
        or any(character not in "0123456789abcdef" for character in source_digest)
    ):
        raise ProjectionViolation(
            "INVALID_SOURCE_DIGEST", "journal source digest is required"
        )
    result: dict[str, Any] = {
        "projection_id": f"projection:{output_digest}",
        "scope": Scope.from_wire(request.get("scope")).to_wire(),
        "session_id": request.get("session_id"),
        "source_cursor": source_cursor.to_wire(),
        "source_digest": source_digest,
        "generation": request.get("generation"),
        "policy_revision": request.get("policy_revision"),
        "mode": request.get("mode"),
        "render_mode": "host_serialized",
        "provider_profile_digest": request.get("provider_profile_digest"),
        "budget_inputs": dict(budget),
        "selected_item_ids": [item["item_id"] for item in normalized_items],
        "selected_summary_ids": [],
        **regions,
        "blocks": blocks,
        "output_digest": output_digest,
        "reason_code": "projection_created",
        "created_at": request.get("created_at"),
    }
    serialized = _canonical(result)
    return ProjectionResult(
        value=result, serialized=serialized, region_bytes=region_bytes_by_kind
    )

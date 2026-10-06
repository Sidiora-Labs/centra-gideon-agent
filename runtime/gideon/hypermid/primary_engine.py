"""Primary-mode ContextEngine backed by the authenticated local Hypermid daemon."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from gideon.cognition.context_engine import AssembledContext, DefaultContextEngine
from gideon.cognition.context_headroom import Component

from .client import HypermidClient, HypermidProtocolError, canonical_json_bytes
from .foundation import Id, Trace
from .models import Cursor, Scope

if TYPE_CHECKING:
    from .context import ConversationContextBridge
    from .writer import WriterCoordinator

_PROJECT_OPERATION = "context.primary_project"
_EXPAND_OPERATION = "context.expand"


class ThreadedPrimaryBridge:
    """Own each async daemon exchange on a worker loop for the synchronous engine seam."""

    def __init__(self, connection_record: str | Path, scope: Scope) -> None:
        self.connection_record = Path(connection_record)
        self.scope = scope
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hypermid-primary")

    def request(
        self, payload: Mapping[str, Any], *, trace: Trace | None = None
    ) -> Mapping[str, Any]:
        return self._request(_PROJECT_OPERATION, payload, trace=trace)

    def expand(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request(_EXPAND_OPERATION, payload)

    def _request(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        trace: Trace | None = None,
    ) -> Mapping[str, Any]:
        result = self._executor.submit(
            self._exchange, operation, dict(payload), trace
        ).result()
        if not isinstance(result, Mapping):
            raise HypermidProtocolError("context response must be an object")
        return result

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _exchange(
        self, operation: str, payload: dict[str, Any], trace: Trace | None
    ) -> object:
        return asyncio.run(self._exchange_async(operation, payload, trace))

    async def _exchange_async(
        self, operation: str, payload: dict[str, Any], trace: Trace | None
    ) -> object:
        client = HypermidClient(self.connection_record, scope=self.scope)
        try:
            await client.connect()
            return await client.request(operation, payload, trace=trace)
        finally:
            await client.close()


class PrimaryContextEngine:
    name = "hypermid-primary"

    def __init__(
        self,
        bridge: ThreadedPrimaryBridge,
        *,
        context_window_tokens: int = 128_000,
        reserved_output_tokens: int = 8_192,
        delegate: DefaultContextEngine | None = None,
        writer: WriterCoordinator | None = None,
        context_bridge: ConversationContextBridge | None = None,
        summary_capability_id: Id | None = None,
        inspection_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        if context_window_tokens < 1 or not 0 <= reserved_output_tokens < context_window_tokens:
            raise ValueError("primary provider budget is invalid")
        self.bridge = bridge
        self.scope = bridge.scope
        self.context_window_tokens = context_window_tokens
        self.reserved_output_tokens = reserved_output_tokens
        self._delegate = delegate or DefaultContextEngine()
        self._writer = writer
        self._context_bridge = context_bridge
        self._summary_capability_id = (
            Id(summary_capability_id) if summary_capability_id is not None else None
        )
        self._inspection_sink = inspection_sink
        self._bridge_sequences: dict[str, int] = {}

    @property
    def owns_compaction(self) -> bool:
        return self._primary_lease() is not None

    def ingest(self, session_key: str, role: str, content: str) -> None:
        self._delegate.ingest(session_key, role, content)
        if self._context_bridge is not None:
            self._context_bridge.sync_session(session_key)

    def assemble(
        self,
        builder: Any,
        text: str,
        *,
        is_new_session: bool,
        **kwargs: Any,
    ) -> AssembledContext:
        from gideon.extensions.apps.app_work import active_for
        work = active_for(str(kwargs.get("session_key") or "default"))
        if work is not None and work.current_tier() == "text":
            from gideon.engine.hooks import HookResult
            return AssembledContext(
                message=text,
                hook_result=HookResult.passthrough(),
                injected_chars=0,
                metadata={"app_task_only": True, "app": work.app},
                components=[Component(name="app task", text=text, compressible=False)],
            )
        from gideon.security.session_credentials import memory_reach
        from gideon.cognition.memory_service import service_for
        provider = service_for(builder.memory).provider
        app_receipt = provider._app_receipt() if getattr(provider, "scope", None) == self.scope and hasattr(provider, "_app_receipt") else None
        target_scope = app_receipt.scope if app_receipt is not None else self.scope
        reach = memory_reach(target_scope, app_receipt=app_receipt)
        kwargs["blocks_writes"] = bool(kwargs.get("blocks_writes")) or not reach.write_allowed
        if not reach.read_allowed:
            restricted = dict(kwargs)
            restricted.update(blocks_reads=True, blocks_writes=True, active_recall=False)
            return self._delegate.assemble(builder, text, is_new_session=is_new_session, **restricted)
        lease = self._primary_lease()
        if lease is None or self._context_bridge is None:
            return self._delegate.assemble(
                builder, text, is_new_session=is_new_session, **kwargs
            )
        session_key = str(kwargs.get("session_key") or "default")
        bridge_sources, bridge_sequence, session_id, bridge_current = (
            self._conversation_sources(session_key, text)
        )
        if bridge_sequence == 0:
            return self._delegate.assemble(
                builder, text, is_new_session=is_new_session, **kwargs
            )
        token = secrets.token_hex(16)
        trace = Trace(Id(f"trace:{token}"), Id(f"request:{token}"))
        response = self.bridge.request(
            self._request_payload(
                session_id=session_id,
                app_receipt=app_receipt,
                shared_memory=app_receipt is not None and provider._shared_reads(),
                new_items=bridge_sources,
                trace=trace,
                writer_lease={
                    "lease_id": str(lease.lease_id),
                    "scope": lease.scope.to_wire(),
                    "fence_epoch": lease.fence_epoch,
                    "fence_token": str(lease.fence_token),
                    "cursor": lease.cursor.to_wire(),
                },
            ),
            trace=trace,
        )
        blocks = self._validated_blocks(response)
        components, covered = self._components(
            response, blocks, excluded_item_id=bridge_current
        )
        source_cursor = self._cursor_text(response["cursor"])
        policy_revision = int(response["projection"]["policy_revision"])
        recall = builder.resolve_scoped_recall(
            text,
            scope=target_scope,
            fallback_cursor=Cursor.from_wire(response["cursor"]),
            policy_revision=policy_revision,
        )
        if recall.component is not None:
            components = (*components, recall.component)
            covered[recall.component.name] = recall.component.covered_digest
        delegated_kwargs = dict(kwargs)
        delegated_kwargs.update(
            hypermid_components=components,
            hypermid_policy_revision=policy_revision,
            hypermid_source_cursor=source_cursor,
            hypermid_covered_digests=covered,
            active_recall=False,
        )
        assembled = self._delegate.assemble(
            builder, text, is_new_session=is_new_session, **delegated_kwargs
        )
        self._bridge_sequences[session_key] = bridge_sequence
        metadata = dict(assembled.metadata)
        metadata["hypermid"] = {
            "projection": response["projection"],
            "serialized_digest": response["serialized"]["serialized_digest"],
            "model_budget": response["serialized"]["model_budget"],
            "cache": response["cache"],
            "reclaim": response["reclaim"],
            "cursor": response["cursor"],
            "writer_fence": response["writer_fence"],
            "writer_fence_epoch": response["writer_fence_epoch"],
            "writer_journal_digest": response["writer_journal_digest"],
            "summary": response["summary"],
            "recall": recall.inspection(),
        }
        result = AssembledContext(
            message=assembled.message,
            hook_result=assembled.hook_result,
            injected_chars=assembled.injected_chars,
            metadata=metadata,
            components=assembled.components,
            notices=assembled.notices,
        )
        if self._inspection_sink is not None:
            from .inspect import WriterStatus, inspect_primary_context

            self._inspection_sink(
                session_id,
                inspect_primary_context(
                    result,
                    scope=self.scope,
                    writer_status=WriterStatus(
                        "hypermid-context",
                        lease.fence_epoch,
                        int(response["projection"]["generation"]),
                    ),
                ),
            )
        return result

    def after_turn(self, session_key: str) -> None:
        self._delegate.after_turn(session_key)

    def close(self) -> None:
        self.bridge.close()

    def _primary_lease(self) -> Any | None:
        if self._writer is None:
            return None
        snapshot = self._writer.snapshot()
        if (
            not snapshot.owns_writes
            or snapshot.lease is None
            or snapshot.lease.scope.to_wire() != self.scope.to_wire()
        ):
            return None
        return snapshot.lease

    def _conversation_sources(
        self, session_key: str, current_text: str
    ) -> tuple[list[dict[str, Any]], int, str, str | None]:
        context_bridge = self._context_bridge
        if context_bridge is None:
            session_id = "session:" + hashlib.sha256(session_key.encode()).hexdigest()
            return [], self._bridge_sequences.get(session_key, 0), session_id, None
        receipt = context_bridge.sync_session(session_key)
        journal = context_bridge.journal(session_key)
        items = journal.all_items()
        previous = self._bridge_sequences.get(session_key, 0)
        selected = items[previous:]
        recovered = (
            context_bridge.recover(
                session_key, item_ids=tuple(item.item_id for item in selected)
            )
            if selected
            else ()
        )
        sources: list[dict[str, Any]] = []
        for entry in recovered:
            item = entry.item.to_mapping()
            item.pop("cursor", None)
            sources.append(
                {
                    "idempotency_key": f"conversation:{entry.item.source_event_id}",
                    "item": item,
                    "source_snapshot": list(entry.source_bytes),
                }
            )
        current_item = None
        if selected:
            latest = selected[-1]
            latest_text = "".join(
                part.text or "" for part in latest.parts if part.kind.value == "text"
            )
            if latest.role.value == "user" and latest_text == current_text:
                current_item = str(latest.item_id)
        return sources, receipt.cursor.sequence, str(receipt.session_id), current_item

    def _request_payload(
        self,
        *,
        session_id: str,
        new_items: Sequence[Mapping[str, Any]],
        trace: Trace,
        writer_lease: Mapping[str, Any],
        app_receipt=None,
        shared_memory: bool = False,
    ) -> dict[str, Any]:
        capabilities = {
            "profile_id": "gideon-host-serialized",
            "context_window_tokens": self.context_window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "roles": ["system", "user", "assistant", "tool"],
            "part_kinds": [
                "text", "reasoning", "tool_call", "tool_result", "image", "file", "context_marker"
            ],
            "requires_tool_adjacency": True,
            "supports_reasoning": True,
            "supports_cache_boundaries": True,
            "max_cache_boundaries": 2,
            "max_images": 64,
            "image_accounting": "host_reported",
        }
        provider_digest = hashlib.sha256(canonical_json_bytes(capabilities)).hexdigest()
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        payload = {
            "session_id": session_id,
            "writer_lease": dict(writer_lease),
            "new_items": list(new_items),
            "expected_source_cursor": None,
            "baseline_through": {"epoch": 1, "sequence": 0},
            "delta_through": {"epoch": 1, "sequence": 0},
            "generation": 1,
            "policy_revision": 1,
            "provider_profile": {**capabilities, "profile_digest": provider_digest},
            "budget_inputs": {
                "context_window_tokens": self.context_window_tokens,
                "reserved_output_tokens": self.reserved_output_tokens,
                "max_input_tokens": self.context_window_tokens - self.reserved_output_tokens,
                "max_items": 100_000,
                "max_images": 64,
            },
            "created_at": now,
            "cache_boundary": None,
            "cached_change": "none",
            "overflow_requires_cached_change": False,
            "reduction_boundary": {
                "kind": "tail_safe",
                "reason_code": "active_tail",
                "cache_generation": 1,
            },
            "reclaim_policy": {
                "advisory_basis_points": 7000,
                "action_basis_points": 8200,
                "emergency_basis_points": 9300,
                "max_rewrite_cost_nanodollars": None,
            },
            "reclaim_candidates": [],
        }
        from gideon.security.session_credentials import memory_reach
        target_scope = app_receipt.scope if app_receipt is not None else self.scope
        capability_id = app_receipt.capability_id if app_receipt is not None else self._summary_capability_id
        if capability_id is not None and memory_reach(target_scope, app_receipt=app_receipt).read_allowed:
            payload["summary_access"] = {
                "capability_id": str(capability_id),
                "request": {
                    "operation": "read",
                    "actor_scope": self.scope.to_wire(),
                    "target_scope": target_scope.to_wire(),
                    "resource_id": "memory-list",
                    "category": "context_summary",
                    "trace": trace.to_wire(),
                },
                "limit": 1_000,
            }
        else:
            payload["summary_access"] = None
        payload["summary_sources"] = []
        if (shared_memory and app_receipt is not None and self._summary_capability_id is not None
                and memory_reach(self.scope, app_receipt=app_receipt).read_allowed):
            payload["summary_sources"].append({
                "capability_id": str(self._summary_capability_id),
                "request": {"operation": "read", "actor_scope": self.scope.to_wire(),
                    "target_scope": self.scope.to_wire(), "resource_id": "memory-list",
                    "category": "context_summary", "trace": trace.to_wire()}, "limit": 1000})
        return payload

    @staticmethod
    def _validated_blocks(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        projection = response.get("projection")
        serialized = response.get("serialized")
        if not isinstance(projection, Mapping) or not isinstance(serialized, Mapping):
            raise HypermidProtocolError("primary response is missing projection data")
        blocks = serialized.get("blocks")
        if (
            not isinstance(blocks, list)
            or not blocks
            or not all(isinstance(block, Mapping) for block in blocks)
        ):
            raise HypermidProtocolError("primary response has no normalized blocks")
        canonical_blocks = json.dumps(
            blocks,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if projection.get("output_digest") != hashlib.sha256(canonical_blocks).hexdigest():
            raise HypermidProtocolError("primary projection output digest does not match blocks")
        digest_input = {
            "blocks": blocks,
            "model_budget": serialized.get("model_budget"),
            "provider_profile_digest": serialized.get("provider_profile_digest"),
            "render_mode": serialized.get("render_mode"),
        }
        expected = hashlib.sha256(canonical_json_bytes(digest_input)).hexdigest()
        if serialized.get("serialized_digest") != expected:
            raise HypermidProtocolError("host serialization digest does not match response")
        return blocks

    @classmethod
    def _components(
        cls,
        response: Mapping[str, Any],
        blocks: Sequence[Mapping[str, Any]],
        *,
        excluded_item_id: str | None,
    ) -> tuple[tuple[Component, ...], dict[str, str]]:
        projection = response["projection"]
        cursor = cls._cursor_text(response["cursor"])
        policy = int(projection["policy_revision"])
        region_items = {
            item_id: region
            for region in ("baseline", "delta", "tail")
            for item_id in projection[region]["item_ids"]
        }
        components: list[Component] = []
        covered: dict[str, str] = {}
        for block in blocks:
            source_ids = tuple(str(value) for value in block.get("source_item_ids", ()))
            if excluded_item_id is not None and excluded_item_id in source_ids:
                continue
            name = str(block["block_id"])
            text = cls._render_block(block)
            region = next(
                (region_items[value] for value in source_ids if value in region_items),
                "baseline",
            )
            coverage = str(block["content_digest"])
            component = Component(
                name=name,
                text=text,
                source="hypermid",
                source_order=len(components),
                content_digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                covered_digest=coverage,
                policy_revision=policy,
                cache_region=region,
                source_cursor=cursor,
                content_type="conversation_history",
            )
            components.append(component)
            covered[name] = coverage
        return tuple(components), covered

    @staticmethod
    def _render_block(block: Mapping[str, Any]) -> str:
        role = str(block.get("role", "system"))
        rendered: list[str] = []
        for part in block.get("parts", ()):
            kind = str(part.get("kind", "text"))
            if kind in {"text", "reasoning", "context_marker"}:
                rendered.append(str(part.get("text", "")))
            elif kind == "tool_call":
                rendered.append(
                    f"[TOOL CALL {part.get('tool_name', '')} {part.get('call_id', '')}]\n"
                    f"{part.get('arguments_json', '')}"
                )
            elif kind == "tool_result":
                rendered.append(
                    f"[TOOL RESULT {part.get('call_id', '')}]\n{part.get('result_json', '')}"
                )
            else:
                rendered.append(
                    f"[{kind.upper()} {part.get('media_type', '')}] {part.get('source_uri', '')}"
                )
        body = "\n".join(rendered)
        return f"[HYPERMID HISTORY role={role}]\n{body}\n[END HYPERMID HISTORY]\n\n"

    @staticmethod
    def _cursor_text(cursor: Mapping[str, Any]) -> str:
        return f"{int(cursor['epoch'])}:{int(cursor['sequence'])}"


__all__ = ["PrimaryContextEngine", "ThreadedPrimaryBridge"]

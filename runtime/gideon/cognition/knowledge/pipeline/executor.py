"""PipelineExecutor — runs a conditional DAG over one item (#30).

Walks the graph in topological order. A node runs only if **all its incoming edges
are satisfied** (an edge is satisfied when its source ran successfully AND, for a
conditional edge, the source's ``classification`` matches ``when``). Each successful
node's output is fed to its successors and (when ``pooled``) appended to the item's
extracted-content pool. A failed or skipped node never aborts the whole item — the
graph continues wherever its dependencies are still met, and the item ends
``done`` (all ran), ``partial`` (some skipped/failed), or ``failed`` (nothing ran).

Concurrency: nodes whose dependencies are all satisfied at the same wave run
concurrently (``asyncio.gather``). Per-node timeout from the node spec.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from gideon.cognition.knowledge.pipeline import outcomes as oc
from gideon.cognition.knowledge.pipeline.graph import PipelineGraph
from gideon.cognition.knowledge.pipeline.outcomes import PhaseOutcome
from gideon.cognition.knowledge.pipeline.registry import (
    get_node, node_available, unavailable_outcome, unserved_reason_sync,
)
from gideon.cognition.knowledge.pipeline.types import NodeContext, NodeOutput, PoolRow

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Outcome of running a graph over one item."""

    outputs: dict[str, NodeOutput] = field(default_factory=dict)
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    not_taken: list[str] = field(default_factory=list)
    outcomes: dict[str, PhaseOutcome] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.ran:
            return "failed"
        if self.skipped or self.failed:
            return "partial"
        return "done"

    def pooled_outputs(self) -> list[NodeOutput]:
        return [o for o in self.outputs.values() if o.success and o.pooled and o.text]

    def pool_rows(self) -> list[PoolRow]:
        """Extra self-named pool rows contributed by successful nodes (see
        :class:`~gideon.cognition.knowledge.pipeline.types.PoolRow`). Emitted in node
        completion order, then in each node's declared row order — the same ordering
        discipline ``pooled_outputs`` already relies on."""
        return [
            row for out in self.outputs.values() if out.success for row in out.pool_rows
        ]


class PipelineExecutor:
    """Run a :class:`PipelineGraph` for one item.

    *params_for* (node_type → execution-param dict) layers user config
    over the graph defaults: ``enabled``, ``backend``, ``use_case``, ``timeout_s``.
    *on_node* (node_type, phase) is called for SSE progress (phase ∈
    queued|running|done|skipped|failed|not_applicable).
    """

    def __init__(self, graph: PipelineGraph, *, params_for=None, on_node=None):
        self._graph = graph
        self._params_for: Callable[[str], dict] = params_for or (lambda nt: {})
        self._on_node = on_node

    async def run(self, ctx: NodeContext) -> ExecutionResult:
        result = ExecutionResult()
        order = self._graph.topo_order()
        await self._run_subset(order, ctx, result)

        for le in self._graph.loop_edges():
            iters = 0
            looped = False
            while iters < le.max_iters:
                src = result.outputs.get(le.from_node)
                if src is None or not src.success or src.classification != le.when:
                    break
                iters += 1
                looped = True
                body = self._loop_body(le.to_node, le.from_node)
                loop_ctx = self._ctx_with_loop(ctx, le, iters, src)
                self._reset_nodes(body, result)
                await self._run_subset(
                    [n for n in order if n in body], loop_ctx, result
                )
                self._notify(le.from_node, "loop")
            if looped:
                body = self._loop_body(le.to_node, le.from_node)
                downstream = [
                    n for n in self._forward_descendants(le.from_node) if n not in body
                ]
                self._reset_nodes(downstream, result)
                await self._run_subset(
                    [n for n in order if n in downstream], ctx, result
                )
        return result

    def _reset_nodes(self, nodes, result: ExecutionResult) -> None:
        """Drop a node set's recorded outputs/phases so a re-run can re-resolve them."""
        for nt in nodes:
            result.outputs.pop(nt, None)
            result.outcomes.pop(nt, None)
            for lst in (result.ran, result.failed, result.skipped, result.not_taken):
                while nt in lst:
                    lst.remove(nt)

    def _forward_descendants(self, start: str) -> set[str]:
        """All nodes reachable from `start` via forward edges (excluding `start`)."""
        seen: set[str] = set()
        stack = [e.to_node for e in self._graph.successors(start)]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            for e in self._graph.successors(n):
                stack.append(e.to_node)
        return seen

    async def _run_subset(
        self, order: list[str], ctx: NodeContext, result: ExecutionResult
    ) -> None:
        """Run the given nodes (a topological sub-order) wave-by-wave — a node is ready
        when every FORWARD predecessor within this subset has resolved."""
        subset = set(order)
        resolved: set[str] = {n for n in self._graph.nodes if n not in subset}
        remaining = list(order)
        while remaining:
            wave = [n for n in remaining if self._deps_resolved(n, resolved)]
            if not wave:
                logger.warning("knowledge pipeline stalled; remaining=%s", remaining)
                for n in remaining:
                    self._skip(n, result, oc.skipped("It never became ready to run."))
                break
            coros = [self._run_one(n, ctx, result) for n in wave]
            await asyncio.gather(*coros)
            resolved.update(wave)
            remaining = [n for n in remaining if n not in resolved]

    def _loop_body(self, start: str, end: str) -> set[str]:
        """Nodes reachable from `start` (the loop target) via forward edges without
        passing `end` (the loop source), plus `end` itself — the segment a loop
        iteration re-runs."""
        body: set[str] = set()
        stack = [start]
        while stack:
            n = stack.pop()
            if n in body:
                continue
            body.add(n)
            if n == end:
                continue
            for e in self._graph.successors(n):
                stack.append(e.to_node)
        body.add(end)
        return body

    def _ctx_with_loop(self, ctx: NodeContext, le, iteration: int, src) -> NodeContext:
        """A per-iteration context carrying the loop's region hints + iteration index
        so the frame sampler tightens density only around the flagged timestamps."""
        params = dict(ctx.params or {})
        params["loop_iteration"] = iteration
        params["dense_regions"] = (src.metadata or {}).get("dense_regions", [])
        return NodeContext(
            item_id=ctx.item_id,
            item_type=ctx.item_type,
            file_path=ctx.file_path,
            content=ctx.content,
            url=ctx.url,
            work_dir=ctx.work_dir,
            params=params,
        )

    def _deps_resolved(self, node_type: str, resolved: set[str]) -> bool:
        return all(e.from_node in resolved for e in self._graph.predecessors(node_type))

    def _edges_satisfied(self, node_type: str, result: ExecutionResult) -> bool:
        """A node runs iff it has at least one satisfied incoming path (or is a root).

        Each incoming edge is satisfied when its source ran successfully and — if
        conditional — the source's classification matches ``when``. A node with NO
        predecessors (root) is always eligible.
        """
        preds = self._graph.predecessors(node_type)
        if not preds:
            return True
        for e in preds:
            src = result.outputs.get(e.from_node)
            if src is None or not src.success:
                continue
            if e.when is None or src.classification == e.when:
                return True
        return False

    def _untaken_outcome(self, node_type: str, result: ExecutionResult) -> PhaseOutcome:
        upstream: dict[str, PhaseOutcome] = {}
        for edge in self._graph.predecessors(node_type):
            output = result.outputs.get(edge.from_node)
            if output is not None and output.success:
                outcome = oc.branch_not_taken(oc.step_name(edge.from_node))
            else:
                outcome = result.outcomes.get(edge.from_node) or oc.skipped("It did not run.")
            upstream.setdefault(edge.from_node, outcome)
        if len(upstream) == 1:
            outcome = next(iter(upstream.values()))
            if outcome.status == oc.NOT_APPLICABLE:
                return outcome
        return oc.waited_on([(oc.step_name(name), outcome) for name, outcome in upstream.items()])

    def _skip(self, node_type: str, result: ExecutionResult, outcome: PhaseOutcome) -> None:
        result.skipped.append(node_type)
        result.outcomes[node_type] = outcome
        self._notify(node_type, outcome.status)

    def _fail(self, node_type: str, result: ExecutionResult, output: NodeOutput) -> None:
        result.outputs[node_type] = output
        result.failed.append(node_type)
        result.outcomes[node_type] = oc.failed(output.error or "It did not finish.")
        self._notify(node_type, oc.FAILED)

    async def _run_one(
        self, node_type: str, ctx: NodeContext, result: ExecutionResult
    ) -> None:
        spec = self._graph.nodes[node_type]
        params = self._params_for(node_type) or {}
        if not params.get("enabled", spec.enabled):
            self._skip(node_type, result, oc.skipped("This step is turned off."))
            return
        if not self._edges_satisfied(node_type, result):
            outcome = self._untaken_outcome(node_type, result)
            result.not_taken.append(node_type)
            result.outcomes[node_type] = outcome
            self._notify(node_type, outcome.status)
            return

        backend = params.get("backend") or spec.backend
        use_case = params.get("use_case", spec.uses_use_case)
        node = get_node(node_type, backend)
        if node is None:
            self._skip(node_type, result, oc.skipped("This step is not available in this install."))
            return
        reason = unserved_reason_sync(use_case)
        if reason:
            self._skip(node_type, result, oc.no_model(use_case or "", reason))
            return
        if not node_available(node):
            self._skip(node_type, result, unavailable_outcome(node))
            return

        self._notify(node_type, "running")
        inputs = {
            edge.from_node: result.outputs[edge.from_node]
            for edge in self._graph.predecessors(node_type)
            if edge.from_node in result.outputs and result.outputs[edge.from_node].success
        }
        timeout_s = float(params["timeout_s"]) if "timeout_s" in params else self._scaled_timeout(node_type, spec, use_case, ctx)
        try:
            output = await asyncio.wait_for(node.run(inputs, ctx), timeout=timeout_s)
        except asyncio.TimeoutError:
            self._fail(node_type, result, NodeOutput(
                node_type=node_type, backend=backend, success=False,
                error=f"It did not finish within {timeout_s:g} seconds, so it was stopped.",
            ))
            return
        except Exception as error:
            logger.exception("knowledge node %s failed", node_type)
            self._fail(node_type, result, NodeOutput(
                node_type=node_type, backend=backend, success=False, error=str(error),
            ))
            return
        if output.success and (ctx.file_path or ctx.params.get('_content_scan_required') or node_type == 'bookmark_scrape'):
            from gideon.workspace.uploads.content_intake import approve_text, IntakeRefused
            import json

            # Segments and self-named pool rows can reach a later model without
            # appearing in the node's primary text. Read those representations too.
            text = '\n\n'.join([output.text, *(row.text for row in output.pool_rows),
                                 json.dumps(output.segments, ensure_ascii=False),
                                 json.dumps(output.metadata, ensure_ascii=False, default=str)])
            try:
                await approve_text(text, surface='knowledge_extraction')
            except IntakeRefused as refused:
                output = NodeOutput(node_type=node_type, backend=backend, success=False,
                                    error=refused.message, metadata={'content_refusal': refused.code})
        if output.success:
            result.outputs[node_type] = output
            result.ran.append(node_type)
            result.outcomes[node_type] = oc.done()
            self._notify(node_type, oc.DONE)
        else:
            self._fail(node_type, result, output)

    _DURATION_SCALED_NODES = frozenset(
        {"transcription", "video_classify", "ocr", "vision", "video_consolidate"}
    )
    _BUDGET_PER_MEDIA_SEC = 2.0
    _MAX_NODE_TIMEOUT_S = 3600.0

    def _scaled_timeout(
        self, node_type: str, spec, use_case, ctx: NodeContext
    ) -> float:
        base = float(spec.timeout_s)
        if node_type not in self._DURATION_SCALED_NODES:
            return base
        dur = self._media_duration(ctx)
        if dur <= 0:
            return base
        scaled = base + dur * self._BUDGET_PER_MEDIA_SEC
        return min(self._MAX_NODE_TIMEOUT_S, max(base, scaled))

    def _media_duration(self, ctx: NodeContext) -> float:
        """Probe the source media's duration in seconds via ffprobe (cached per run).
        Returns 0 when unavailable (no ffprobe / not media / probe failure)."""
        cached = getattr(self, "_dur_cache", None)
        if cached is not None:
            return cached
        dur = 0.0
        path = ctx.file_path or ""
        if path:
            import shutil
            import subprocess

            ffprobe = shutil.which("ffprobe")
            if ffprobe:
                try:
                    out = subprocess.run(
                        [
                            ffprobe,
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            path,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    dur = float((out.stdout or "").strip() or 0)
                except (ValueError, OSError, subprocess.SubprocessError):
                    dur = 0.0
        self._dur_cache = dur
        return dur

    def _notify(self, node_type: str, phase: str) -> None:
        if self._on_node:
            try:
                self._on_node(node_type, phase)
            except Exception:
                logger.debug("pipeline on_node callback failed", exc_info=True)

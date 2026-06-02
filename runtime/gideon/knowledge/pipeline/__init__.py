"""Knowledge ingestion node-graph engine (#30).

A conditional DAG ingestion pipeline: an item's ``type`` selects a code-owned
:class:`~gideon.knowledge.pipeline.graph.PipelineGraph`; the
:class:`~gideon.knowledge.pipeline.executor.PipelineExecutor` runs its nodes
(branching on classifications, fanning out/in), each node's output lands in the
item's extracted-content pool, then terminal stages (insights → chunk+embed) run
over the whole bundle. See ``runner.ingest_item`` for the orchestration entry point.
"""

from __future__ import annotations

from gideon.knowledge.pipeline.graph import NodeSpec, PipelineGraph, PipelineGraphError
from gideon.knowledge.pipeline.graphs import graph_for
from gideon.knowledge.pipeline.types import Edge, NodeContext, NodeOutput, ProcessingNode

_REGISTERED = False


def ensure_nodes_registered() -> None:
    """Idempotently register all built-in node implementations."""
    global _REGISTERED
    if _REGISTERED:
        return
    from gideon.knowledge.pipeline.nodes import media_nodes, text_nodes

    text_nodes.register()
    media_nodes.register()
    _REGISTERED = True


__all__ = [
    "NodeSpec",
    "PipelineGraph",
    "PipelineGraphError",
    "graph_for",
    "Edge",
    "NodeContext",
    "NodeOutput",
    "ProcessingNode",
    "ensure_nodes_registered",
]

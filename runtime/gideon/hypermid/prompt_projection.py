from __future__ import annotations

from dataclasses import dataclass

from .search import SearchEngine, SearchResponse


@dataclass(frozen=True, slots=True)
class PromptMemory:
    content: str
    source_labels: tuple[str, ...]
    response: SearchResponse


def project_automatic_memory(engine: SearchEngine, response: SearchResponse) -> PromptMemory:
    engine.acknowledge_delivery(response, automatic=True)
    blocks = []
    labels = []
    for hit in response.hits:
        labels.append(f"{hit.source}:{hit.id}")
        blocks.append(f"[{hit.source}:{hit.kind}] {hit.content}")
    return PromptMemory(content="\n\n".join(blocks), source_labels=tuple(labels), response=response)


def acknowledge_explicit_memory(engine: SearchEngine, response: SearchResponse) -> None:
    engine.acknowledge_delivery(response, automatic=False)

from __future__ import annotations

import pytest

from gideon.hypermid.lineage import (
    dependent_descendants,
    recover_provenance,
    require_acyclic_lineage,
)
from gideon.hypermid.protocol import Scope
from gideon.hypermid.records import (
    LineageEdge,
    LineageRelation,
    MemoryContractError,
    ProvenanceSpan,
    SourceKind,
    SourceSnapshot,
    content_digest,
)


SCOPE = Scope("owner-1", "project-1")
DIGEST = "a" * 64


def test_raw_source_recovery_remains_exact_for_tombstoned_record_lineage() -> None:
    raw = "The original source remains authoritative."
    source = SourceSnapshot(
        source_id="source-1",
        owner_scope=SCOPE,
        kind=SourceKind.MESSAGE,
        source_digest=content_digest(raw),
        locator="session-1:message-1",
        captured_content=raw,
        capture_method="journal-copy",
        observed_at_ms=10,
    )
    start = raw.encode().index(b"original")
    end = start + len(b"original source")
    span = ProvenanceSpan(
        source_id="source-1",
        span_start=start,
        span_end=end,
        quoted_digest=content_digest("original source"),
    )

    assert recover_provenance((span,), {source.source_id: source}) == (
        "original source",
    )


def test_source_invalidation_walks_all_digest_dependent_descendants() -> None:
    edges = (
        ("summary-1", "anchor-1", LineageRelation.DERIVED_FROM),
        ("summary-2", "summary-1", LineageRelation.DERIVED_FROM),
        ("citation-1", "anchor-1", LineageRelation.CITES),
        ("merged-1", "summary-2", LineageRelation.MERGED_FROM),
    )

    assert dependent_descendants(("anchor-1",), edges) == (
        "merged-1",
        "summary-1",
        "summary-2",
    )


def test_cycle_insertion_is_refused_for_derived_lineage() -> None:
    existing = {
        "record-b": (LineageEdge("record-a", LineageRelation.DERIVED_FROM, DIGEST),),
    }
    proposed = (LineageEdge("record-b", LineageRelation.DERIVED_FROM, DIGEST),)

    with pytest.raises(MemoryContractError, match="cycle"):
        require_acyclic_lineage("record-a", proposed, existing)


def test_non_derivation_cross_links_do_not_create_a_cycle() -> None:
    existing = {
        "record-b": (LineageEdge("record-a", LineageRelation.CITES, DIGEST),),
    }
    require_acyclic_lineage(
        "record-a",
        (LineageEdge("record-b", LineageRelation.CONTRADICTS, DIGEST),),
        existing,
    )


def test_utf8_source_span_must_not_split_a_code_point() -> None:
    raw = "A→B"
    source = SourceSnapshot(
        source_id="source-utf8",
        owner_scope=SCOPE,
        kind=SourceKind.EXTERNAL,
        source_digest=content_digest(raw),
        locator=None,
        captured_content=raw,
        capture_method="snapshot",
        observed_at_ms=10,
    )

    with pytest.raises(MemoryContractError, match="UTF-8"):
        ProvenanceSpan("source-utf8", 1, 2).recover(source)

"""Degraded-contract coverage lint (PLATFORM-RESILIENCE §5).

The floor doctrine: "a lint test asserts every ``use_case=`` call site maps to a
registered contract — the mechanism that keeps FUTURE surfaces honest." Concretely:
every source file that calls ``one_shot_completion(`` (a non-interactive model call)
must map to a registered ``DegradedContract`` surface, so a new model-dependent
surface can't ship without declaring its no-model floor.

Mapping is by call-site FILE, not by the ``use_case=`` string: the informal labels
``"background"``/``"ingestion"`` both collapse to the ``reasoning`` axis inside
``one_shot_completion`` (verified), so the use-case string is not a stable surface
key — the owning file is.
"""

from __future__ import annotations

import pathlib
import re

from gideon.operations.resilience import degraded

_SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "runtime" / "gideon"

_CALL_SITE_SURFACES = {
    "inbox_service.py": "inbox_enrichment",
    "after_turn_review.py": "memory_extraction",
    "knowledge/llm_pool.py": "knowledge_ingest",
    "knowledge/source_digest.py": "source_digest",
    "knowledge/updates.py": "synthesis_watchers",
    "proactive/pipeline.py": "triage_digest",
    "action_providers/browse_provider.py": "browse",
    "nl_to_cron.py": "assistant_reasoning",
    "learning_report.py": "assistant_reasoning",
    "context.py": "assistant_reasoning",
    "visualize.py": "assistant_reasoning",
    "mcp_workflows.py": "assistant_reasoning",
    "integrations/web/fetch.py": "assistant_reasoning",
    "action_providers/knowledge_report_provider.py": "research_report",
    "evals/judge_bench.py": "assistant_reasoning",
    "evals/study_arms.py": "assistant_reasoning",
    "evals/bakeoff.py": "assistant_reasoning",
    "dashboard/chat_retag.py": "assistant_reasoning",
    "dashboard/chat_handlers.py": "assistant_reasoning",
    "dashboard/handlers/loop_routes.py": "assistant_reasoning",
    "tool_providers/prose_compress.py": "assistant_reasoning",
    "dashboard/handlers/doctor.py": "chat",
    "notification_verify.py": "assistant_reasoning",
    "durability/conflict_merge.py": "assistant_reasoning",
    "sampling.py": "assistant_reasoning",
    "packs/prompt_cards.py": "assistant_reasoning",
}

_CALL_RE = re.compile(r"\bone_shot_completion\s*\(")


def _files_calling_one_shot() -> set[str]:
    """Repo-relative (posix) paths of every source file that CALLS
    one_shot_completion — excluding llm_helpers.py, which DEFINES it."""
    hits: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if path.name == "llm_helpers.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _CALL_RE.search(text):
            hits.add(path.relative_to(_SRC).as_posix())
    return hits


def test_mapped_surfaces_are_all_registered():
    """Every surface named in the map must be a real registered contract."""
    registered = {c.surface for c in degraded.all_contracts()}
    for path, surface in _CALL_SITE_SURFACES.items():
        assert surface in registered, f"{path} maps to unregistered surface {surface!r}"


def test_every_one_shot_call_site_is_mapped():
    """Every file calling one_shot_completion must appear in the map — so a new
    model-dependent surface can't ship without declaring its degraded floor."""
    called = _files_calling_one_shot()
    mapped = set(_CALL_SITE_SURFACES)
    unmapped = {p for p in called if not any(p.endswith(m) for m in mapped)}
    assert not unmapped, (
        "New one_shot_completion call-site(s) with no degraded-contract mapping: "
        f"{sorted(unmapped)}. Add each file to _CALL_SITE_SURFACES in this test AND "
        "ensure it maps to a registered DegradedContract surface (PLATFORM-RESILIENCE §5)."
    )


def test_map_has_no_stale_entries():
    """A file in the map that no longer calls one_shot_completion is stale — remove it
    (keeps the ratchet honest in both directions)."""
    called = _files_calling_one_shot()
    stale = {m for m in _CALL_SITE_SURFACES if not any(p.endswith(m) for p in called)}
    assert (
        not stale
    ), f"Stale _CALL_SITE_SURFACES entries (file no longer calls one_shot): {sorted(stale)}"

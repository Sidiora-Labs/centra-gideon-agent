import asyncio
import os
import time
from functools import wraps
from gideon.security.approval_answer import YOU
from gideon.security.session_credentials import begin_turn, end_turn
from gideon.cognition.consolidation_cycle import ConsolidationUnavailable

def bound(key):
    def decorate(function):
        @wraps(function)
        async def invoke(*args, **kwargs):
            credential = begin_turn(key, YOU, turn_id="consolidation-test", memory_mode="persistent")
            try:
                return await function(*args, **kwargs)
            finally:
                end_turn(credential)
        return invoke
    return decorate

import pytest

from gideon.cognition.consolidation_cycle import ConsolidationRound
from gideon.cognition.history import (
    _CONSOLIDATION_THRESHOLD,
    ConversationLog,
    HistoryConsolidator,
)
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.concurrency import single_flight
from gideon.core.config.loader import AppConfig
from gideon.extensions.skills import AutoSkillProvenance, ProcedureLibrary


@pytest.fixture
def local_consolidator(tmp_path, monkeypatch):
    home = tmp_path / "owned-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    settings = AppConfig.load()
    settings.learning.enabled = False
    settings.learning.replay_enabled = False
    settings.memory.graph_enabled = False
    settings.external_access.capture.retention_days = 1
    settings.save()
    memory = MemoryJournal(workspace=home / "workspace")
    memory.init()
    records = SemanticArchive(db_path=home / "memory.db", embedding_dim=3)
    records.init()
    memory.vector_store = records
    journal = ConversationLog(base_dir=home / "sessions")
    journal.init()
    skills = ProcedureLibrary(skills_path=home / "skills", install_builtins=False)
    owner = HistoryConsolidator(
        journal,
        memory,
        vector_store=records,
        skills_loader=skills,
        auto_skills_enabled=True,
        auto_refine_enabled=True,
        auto_min_tool_calls=1,
    )
    yield owner, home
    records.close()


def _round(owner, key="dashboard:owned", history=True):
    owner._log.append(key, "assistant", "Read the project notes.", tools=["fs_read"])
    extraction = ConsolidationRound(owner, key, history)
    assert extraction.load()
    return extraction


@pytest.mark.asyncio
@bound("pending")
async def test_real_background_task_accounts_for_empty_model_result(local_consolidator):
    owner, _ = local_consolidator
    for index in range(_CONSOLIDATION_THRESHOLD):
        owner._log.append("pending", "user", f"message {index}")
    owner.maybe_consolidate("pending")
    assert owner._running == {"pending"}
    tasks = tuple(owner._tasks)
    assert len(tasks) == 1
    owner.maybe_consolidate("pending")
    assert tuple(owner._tasks) == tasks
    result = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(result[0], ConsolidationUnavailable)
    assert not owner._tasks and not owner._running
    assert not owner._prefs_offset
    assert "pending" in owner._schedule.debt
    assert owner._log.unconsolidated_count("pending") == _CONSOLIDATION_THRESHOLD
    owner.maybe_consolidate("pending")
    assert not owner._tasks


@pytest.mark.asyncio
@bound("invalid")
async def test_real_invalid_transcript_task_preserves_retry_position(
    local_consolidator,
):
    owner, _ = local_consolidator
    for index in range(_CONSOLIDATION_THRESHOLD):
        owner._log.append("invalid", "assistant", str(index), tools=[7])
    owner.maybe_consolidate("invalid")
    result = await asyncio.gather(*tuple(owner._tasks), return_exceptions=True)
    assert len(result) == 1 and isinstance(result[0], TypeError)
    assert not owner._tasks and not owner._running and not owner._prefs_offset
    assert owner._log.unconsolidated_count("invalid") == _CONSOLIDATION_THRESHOLD


@pytest.mark.asyncio
@bound("locked")
async def test_owned_flock_blocks_duplicate_consolidation(local_consolidator):
    owner, _ = local_consolidator
    owner._log.append("locked", "user", "preserve")
    with single_flight("consolidate:locked") as acquired:
        assert acquired
        assert await owner.consolidate_now("locked") is False
        assert "locked" in owner._schedule.debt
    assert not owner._running
    assert owner._log.unconsolidated_count("locked") == 1


@pytest.mark.asyncio
@bound("cancelled")
async def test_cancelled_before_start_keeps_existing_admission_semantics(
    local_consolidator,
):
    owner, _ = local_consolidator
    for index in range(_CONSOLIDATION_THRESHOLD):
        owner._log.append("cancelled", "user", str(index))
    owner.maybe_consolidate("cancelled")
    (task,) = owner._tasks
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not owner._tasks and not owner._prefs_offset
    assert not owner._running
    assert "cancelled" in owner._schedule.debt


@pytest.mark.asyncio
@bound("idle")
async def test_idle_activity_and_history_cooldown_use_real_tasks(local_consolidator):
    owner, _ = local_consolidator
    owner._history_idle_secs = 10
    owner._log.append("idle", "user", "pending")
    owner._last_activity["idle"] = time.time() - 20
    owner.check_idle_sessions()
    assert len(owner._tasks) == 1
    result = await asyncio.gather(*tuple(owner._tasks), return_exceptions=True)
    assert isinstance(result[0], ConsolidationUnavailable)
    assert "idle" not in owner._history_consolidated
    owner.check_idle_sessions()
    assert not owner._tasks
    assert owner._log.unconsolidated_count("idle") == 1


def test_prompt_composition_tracks_local_configuration(local_consolidator):
    owner, _ = local_consolidator
    extraction = _round(owner)
    settings = AppConfig.load()
    settings.memory.proactive_commitments = True
    settings.memory.proactive_commitments_max_per_day = 2
    settings.memory.holder_attribution = True
    settings.save()
    from gideon.integrations.prompt_providers.runtime import render_snippet_block

    prompt = extraction.render()
    for key in (
        "history",
        "semantic",
        "episodic",
        "claims",
        "preferences",
        "projects",
        "lessons",
        "self-persona",
        "new-skill",
        "refined-skill",
    ):
        assert render_snippet_block("consolidation-key-" + key) in prompt
    assert (
        render_snippet_block("consolidation-key-commitments", {"max_commitments": 2})
        in prompt
    )
    assert "ASSISTANT [tools: fs_read]: Read the project notes." in prompt
    owner._migrated = True
    owner._auto_refine_enabled = False
    prompt = extraction.render()
    assert "## Current Preferences" not in prompt
    assert render_snippet_block("consolidation-key-refined-skill") not in prompt
    owner._log.append(
        "sensitive", "assistant", "secret access", tools=["cat ~/.aws/credentials"]
    )
    sensitive = ConsolidationRound(owner, "sensitive", True)
    assert sensitive.load()
    assert render_snippet_block("consolidation-key-new-skill") not in sensitive.render()


@pytest.mark.asyncio
@bound("dashboard:owned")
async def test_extracted_result_writes_real_markdown_and_memory_records(
    local_consolidator,
):
    owner, _ = local_consolidator
    extraction = _round(owner, history=False)
    extraction.render()
    await extraction.apply(
        {
            "preferences_update": "Prefer numbered implementation steps.",
            "projects_update": "The workshop scheduler is underway.",
            "episodic": [
                {
                    "text": "The user chose a numbered scheduler plan.",
                    "tags": ["planning"],
                    "importance": 0.7,
                }
            ],
            "lessons": [
                {
                    "rule": "Read existing scheduling constraints before proposing dates.",
                    "category": "workflow",
                }
            ],
        }
    )
    assert "numbered implementation" in owner._memory.read_preferences()
    assert "workshop scheduler" in owner._memory.read_projects()
    rows = owner._svc.get_records()
    assert any("scheduler plan" in row.text for row in rows)
    assert any("scheduling constraints" in row.text for row in rows)
    assert owner._log.unconsolidated_count("dashboard:owned") == 1


@pytest.mark.asyncio
@bound("dashboard:owned")
async def test_history_application_marks_offset_and_prunes_actual_capture_files(
    local_consolidator,
):
    owner, _ = local_consolidator
    from gideon.integrations.inbound.capture_store import capture_dir

    root = capture_dir()
    root.mkdir(parents=True, exist_ok=True)
    expired, fresh = root / "expired.jsonl", root / "fresh.jsonl"
    expired.write_text("{}\n")
    fresh.write_text("{}\n")
    old = time.time() - 3 * 86400
    os.utime(expired, (old, old))
    extraction = _round(owner)
    extraction.render()
    await extraction.apply({"history_entry": "Agreed on the workshop milestones."})
    assert "workshop milestones" in owner._memory.read_history()
    assert "workshop milestones" in owner._svc.working_memory("dashboard:owned")
    assert owner._log.unconsolidated_count("dashboard:owned") == 0
    assert fresh.exists() and not expired.exists()
    assert owner._consolidation_count == 0


@pytest.mark.asyncio
@bound("busy")
async def test_session_sealing_uses_durable_records_even_when_admission_declines(
    local_consolidator,
):
    owner, _ = local_consolidator
    owner._svc.write_working_memory("busy", "The next milestone is ready for review.")
    owner._running.add("busy")
    assert await owner.consolidate_session("busy") is False
    assert "ready for review" in owner._svc.working_memory("busy")
    assert not [row for row in owner._svc.get_records() if "sealed" in row.tags]
    assert "busy" in owner._schedule.debt


def test_persona_and_commitment_capacity_use_actual_store(local_consolidator):
    owner, _ = local_consolidator
    settings = AppConfig.load()
    settings.memory.proactive_commitments_max_per_day = 2
    settings.save()
    owner._write_self_persona(
        {
            "self_persona": [
                "I explain assumptions clearly.",
                "I protect AKIAIOSFODNN7EXAMPLE from disclosure.",
                9,
                "I summarize decisions.",
                "outside the cap",
            ]
        },
        "helper",
    )
    persona = owner._svc.get_records(kinds={"self_persona"})
    assert len(persona) == 3
    assert all(row.scope_ref == "helper" for row in persona)
    assert all("AKIAIOSFODNN7EXAMPLE" not in row.text for row in persona)
    owner._write_commitments(
        {
            "commitments": [
                {
                    "text": "Check on workshop preparation.",
                    "due_window": "2030-01-01",
                    "confidence": 0.9,
                },
                {
                    "text": "Uncertain follow-up",
                    "due_window": "2030-01-02",
                    "confidence": "invalid",
                },
                {
                    "text": "outside extracted cap",
                    "due_window": "2030-01-03",
                    "confidence": 0.99,
                },
            ]
        },
        "helper",
        "dashboard_owned",
    )
    commitments = owner._svc.get_records(kinds={"commitment"})
    assert len(commitments) == 1
    assert commitments[0].value["channel"] == "dashboard:owned"
    assert commitments[0].value["text"] == "Check on workshop preparation."


def test_skill_creation_is_proposed_and_enabled_refinement_updates_auto_only(
    local_consolidator,
):
    owner, home = local_consolidator
    from gideon.extensions.skills import proposals

    owner._process_auto_skills(
        {
            "new_skill": {
                "slug": "measure-shelf",
                "description": "Measure shelf clearances before cutting wood.",
                "triggers": "shelf clearance",
                "procedure_md": "1. Measure width.\n2. Record clearance.",
            }
        },
        "owned",
    )
    pending = proposals.list_pending()
    assert len(pending) == 1 and pending[0].slug == "measure-shelf"
    assert owner._skills_loader.list_auto_skills() == []
    name = owner._skills_loader.create_auto_skill(
        "pack-crate",
        description="Pack a crate.",
        triggers="crate",
        procedure_md="1. Select a crate.",
        provenance=AutoSkillProvenance("seed", AutoSkillProvenance.now_iso()),
    )
    assert name == "auto/pack-crate"
    owner._process_auto_skills(
        {
            "refined_skill": {
                "name": name,
                "description": "Pack fragile parts in a crate.",
                "triggers": "crate parts",
                "procedure_md": "1. Select a crate.\n2. Pad every part.",
            }
        },
        "refiner",
    )
    written = (home / "skills" / name / "SKILL.md").read_text()
    assert "Pad every part" in written and "session_key: refiner" in written
    manual = home / "skills" / "manual" / "SKILL.md"
    manual.parent.mkdir()
    manual.write_text(
        "---\nname: manual\ndescription: Operator procedure\n---\nKeep this."
    )
    before = manual.read_bytes()
    owner._process_auto_skills(
        {
            "refined_skill": {
                "name": "manual",
                "description": "changed",
                "procedure_md": "replace",
            }
        },
        "refiner",
    )
    assert manual.read_bytes() == before


def test_curator_tick_prunes_real_surfacing_history(local_consolidator):
    owner, _ = local_consolidator
    from gideon.cognition.learning.surfacing_events import (
        SurfacingEvent,
        SurfacingEventStore,
    )
    from gideon.cognition.learning.usage import UsageStore

    settings = AppConfig.load()
    settings.learning.enabled = True
    settings.learning.curator_enabled = True
    settings.save()
    events = SurfacingEventStore()
    usage = UsageStore()
    try:
        usage.record(
            kind="skill",
            entity="operator-owned",
            event="loaded",
            source_type="user",
            immediate=True,
        )
        events.record(
            [
                SurfacingEvent(
                    "skill", "expired", created_ts=time.time() - 100 * 86400
                ),
                SurfacingEvent("skill", "current"),
            ]
        )
        assert owner._run_learning_curator() == ""
        assert [row.entity for row in events.read()] == ["current"]
        assert [(row.entity, row.used) for row in usage.list_kind("skill")] == [
            ("operator-owned", 1)
        ]
    finally:
        events.close()
        usage.close()

@pytest.mark.asyncio
@bound("dashboard:owned")
async def test_failed_formation_retains_sources_and_avoids_fallback(local_consolidator):
    owner, _ = local_consolidator
    extraction = _round(owner)
    extraction.render()
    with pytest.raises(ConsolidationUnavailable):
        await extraction.apply({"history_entry": "must not commit", "semantic": [{"key": "project.test", "value": "must not fallback", "confidence": 0.95}]})
    assert owner._log.unconsolidated_count("dashboard:owned") == 1
    assert "must not commit" not in owner._memory.read_history()
    assert not owner._svc.get_records()

@pytest.mark.asyncio
async def test_unbound_work_is_terminal_policy_denial(local_consolidator):
    owner, _ = local_consolidator
    owner._log.append("unbound", "user", "private pending")
    assert await owner.consolidate_session("unbound") is False
    assert "unbound" in owner._schedule.denied
    assert "unbound" not in owner._schedule.debt
    assert owner._log.unconsolidated_count("unbound") == 1

@pytest.mark.asyncio
@bound("actor-a")
async def test_owner_grant_cannot_write_unrelated_session(local_consolidator):
    owner, _ = local_consolidator
    owner._log.append("actor-b", "user", "private pending")
    assert await owner.consolidate_now("actor-b") is False
    assert "actor-b" in owner._schedule.denied
    assert "actor-b" not in owner._schedule.debt

@pytest.mark.asyncio
@bound("process-locked")
async def test_external_process_lease_preserves_debt_and_prevents_seal(local_consolidator):
    import sys
    owner, _ = local_consolidator
    owner._log.append("process-locked", "user", "pending source")
    code = "from gideon.core.concurrency import single_flight; import sys; lease=single_flight('consolidate:process-locked'); acquired=lease.__enter__(); print(acquired, flush=True); sys.stdin.readline(); lease.__exit__(None,None,None)"
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", code, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    try:
        assert await asyncio.wait_for(process.stdout.readline(), timeout=10) == b"True\n"
        assert await owner.consolidate_session("process-locked") is False
        assert "process-locked" in owner._schedule.debt
        assert owner._log.get_metadata("process-locked")[owner._schedule.SEAL_PENDING]
        assert owner._log.unconsolidated_count("process-locked") == 1
    finally:
        process.stdin.write(b"release\n")
        await process.stdin.drain()
        await asyncio.wait_for(process.wait(), timeout=10)

@pytest.mark.asyncio
async def test_queued_turn_revocation_denies_without_retry(local_consolidator):
    owner, _ = local_consolidator
    credential = begin_turn("revoked", YOU, turn_id="revoked", memory_mode="persistent")
    for index in range(_CONSOLIDATION_THRESHOLD):
        owner._log.append("revoked", "user", str(index))
    await owner._schedule.concurrent.acquire()
    await owner._schedule.concurrent.acquire()
    owner.maybe_consolidate("revoked")
    tasks = tuple(owner._tasks)
    end_turn(credential)
    owner._schedule.concurrent.release()
    owner._schedule.concurrent.release()
    result = await asyncio.gather(*tasks, return_exceptions=True)
    from gideon.cognition.consolidation_cycle import ConsolidationPolicyDenied
    assert isinstance(result[0], ConsolidationPolicyDenied)
    assert "revoked" in owner._schedule.denied
    assert "revoked" not in owner._schedule.debt
    assert not owner._running
    assert owner._log.unconsolidated_count("revoked") == _CONSOLIDATION_THRESHOLD


def test_recovery_reads_canonical_pending_sources(local_consolidator):
    from gideon.cognition.consolidation_cycle import ConsolidationTasks
    owner, _ = local_consolidator
    owner._log.append("recover", "user", "still owed")
    restarted = ConsolidationTasks(owner)
    restarted.recover()
    assert "recover" in restarted.debt
    assert restarted.debt["recover"]["history"]
    assert restarted.debt["recover"]["due"] <= time.monotonic()

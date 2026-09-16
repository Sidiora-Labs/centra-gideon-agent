"""Self-QA actions against real repositories, evidence, ledgers and native stores."""

import asyncio
import hashlib
import json
import subprocess

import pytest

from gideon.assurance.selfqa import findings, watch
from gideon.automation.triggers.service import to_epoch
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows import defs, effects
from gideon.automation.workflows import store as runs
from gideon.automation.workflows.journal import ledger
from gideon.automation.workflows.models import RunStatus
from gideon.automation.workflows.native_defs import NativeWorkflowDefProvider
from gideon.automation.workflows.watchdog import WorkflowWatchdog
from gideon.cognition import knowledge
from gideon.core.config.loader import AppConfig, RemediationConfig
from gideon.engine.session import ConversationDirectory
from gideon.engine.tasks import registry as tasks
from gideon.integrations.action_providers import registry
from gideon.integrations.action_providers import remediation_provider as remediation
from gideon.integrations.action_providers import selfqa_evidence_provider as evidence
from gideon.integrations.action_providers import selfqa_finding_provider as finding
from gideon.integrations.action_providers import selfqa_triage_provider as triage
from gideon.integrations.action_providers import selfqa_watch_provider as watching
from gideon.integrations.action_providers import services
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.action_providers.run_workflow_provider import (
    RunWorkflowActionProvider,
)
from gideon.integrations.inbox_providers import native_source
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.operations.resilience.remediation import RunResult
from gideon.security import trust_mode
from gideon.security.guardrails import budgets
from gideon.workspace.artifacts import registry as artifacts

CTX = ActionContext("workflow_node")


@pytest.fixture(autouse=True)
def qa_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "config.json").write_text('{"providers": []}')
    for module, name, value in (
        (registry, "_providers", {}),
        (services, "_services", None),
        (tasks, "_providers", {}),
        (artifacts, "_providers", {}),
        (defs, "_providers", {}),
        (effects, "START_DEDUPE", effects.CallerDedupe()),
        (native_source, "_dashboard_state", None),
        (findings, "_filed", {}),
        (knowledge, "_store", None),
        (budgets, "_METER", budgets.SpendMeter(config_dir=tmp_path)),
    ):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(
        trust_mode._TRUST, "_on_disable", list(trust_mode._TRUST._on_disable)
    )
    yield tmp_path
    if knowledge._store is not None:
        knowledge._store.close()


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo, relative, content):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "--", relative)
    git(repo, "commit", "-q", "-m", f"change {relative}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(qa_home):
    repo = qa_home / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Local QA")
    git(repo, "config", "user.email", "qa@example.invalid")
    commit(repo, "README.md", "initial\n")
    return repo


def wire_state(supervisor=None):
    state = ConsoleState(sessions=ConversationDirectory(AppConfig.load()), start_time=0)
    services.set_action_services(
        services.ActionServices(state, asyncio.create_task, workflows=supervisor)
    )
    native_source.set_dashboard_state(state)
    return state


def configure_remediation(home, **values):
    (home / "config.json").write_text(
        json.dumps({"providers": [], "resilience": {"remediation": values}})
    )
    return AppConfig.load().resilience.remediation


@pytest.mark.parametrize(
    "healthy,degraded,want",
    [(0, 0, (3600, 300)), (-4, -1, (60, 60)), (2, 8, (120, 480))],
)
def test_adaptive_cadence_uses_real_config_bounds(healthy, degraded, want):
    cfg = RemediationConfig(
        idle_minutes_healthy=healthy, tick_minutes_degraded=degraded
    )
    assert remediation._cadence_secs(cfg) == want


def test_adaptive_rearm_updates_the_actual_clock_and_preserves_run_state_on_reconcile(
    qa_home,
):
    cfg = configure_remediation(
        qa_home, enabled=True, idle_minutes_healthy=12, tick_minutes_degraded=2
    )
    store = TriggerStore(base_dir=qa_home)
    remediation.reconcile_remediation_trigger(store)
    trigger = store.get(remediation.REMEDIATION_TRIGGER_ID).trigger
    trigger.spec["timezone"] = "UTC"
    store.upsert(trigger)
    now = 1800000000.0
    assert remediation._rearm(healthy=False, cfg=cfg, now=now) == "2m"
    degraded = store.get(remediation.REMEDIATION_TRIGGER_ID).trigger
    assert to_epoch(degraded.next_fire_at) == now + 120
    assert degraded.spec["health_state"] == "degraded"
    remediation.reconcile_remediation_trigger(store)
    assert (
        store.get(remediation.REMEDIATION_TRIGGER_ID).trigger.spec["health_state"]
        == "degraded"
    )
    assert remediation._rearm(healthy=True, cfg=cfg, now=now) == "12m"
    healthy = store.get(remediation.REMEDIATION_TRIGGER_ID).trigger
    assert to_epoch(healthy.next_fire_at) == now + 720
    assert healthy.spec["timezone"] == "UTC"
    assert healthy.capabilities and healthy.delivery == "inbox"
    configure_remediation(qa_home, enabled=False)
    remediation.reconcile_remediation_trigger(store)
    assert store.get(remediation.REMEDIATION_TRIGGER_ID).trigger.enabled is False


@pytest.mark.asyncio
async def test_remediation_config_off_then_real_measurement_with_no_job_budget(qa_home):
    configure_remediation(qa_home, enabled=False)
    provider = remediation.SelfRemediationActionProvider()
    result = await provider.execute({}, CTX)
    assert result.success and result.stdout == "remediation: disabled by config"
    configure_remediation(qa_home, enabled=True, target_score=0, max_cost_usd=0)
    store = TriggerStore(base_dir=qa_home)
    remediation.reconcile_remediation_trigger(store)
    completed = await provider.execute({}, CTX)
    assert completed.success, completed.error
    assert (
        "target_score already met" in completed.stdout
        and "0 job(s)" in completed.stdout
    )
    assert store.get(remediation.REMEDIATION_TRIGGER_ID).trigger.next_fire_at
    assert not (qa_home / "model_calls.jsonl").exists()


def test_remediation_typed_failed_jobs_remain_visible_even_without_rearm():
    result = remediation._pass_result(
        RunResult(
            45,
            73,
            jobs=[
                {"id": "one", "status": "error"},
                {"id": "two", "status": "ok"},
                {"id": "three", "status": "error"},
            ],
        ),
        "",
    )
    assert not result.success and result.exit_code == 1
    assert result.error.endswith("one, three")
    assert "45→73" in result.stdout and "3 job(s); next in unchanged" in result.stdout


@pytest.mark.parametrize(
    "value,want",
    [
        (True, True),
        (False, False),
        (1, True),
        (" YES ", True),
        ("true", True),
        ("false", False),
        ("2", False),
        (None, False),
    ],
)
def test_evidence_binding_boolean_spellings(value, want):
    assert evidence._as_bool(value) is want


@pytest.mark.asyncio
async def test_evidence_seals_actual_bytes_and_preserves_partial_bundle(qa_home):
    workspace = qa_home / "run"
    bundle = workspace / "proof"
    bundle.mkdir(parents=True)
    content = b"the local action completed\n"
    (bundle / "run.log").write_bytes(content)
    context = ActionContext(
        "workflow_node",
        payload={"workspace": str(workspace), "project_id": "local-project"},
    )
    provider = evidence.SelfQaEvidenceActionProvider()
    partial = await provider.execute(
        {"bundle_subdir": "proof", "scenario_id": "s1", "sha": "a" * 40}, context
    )
    assert not partial.success
    data = json.loads(partial.stdout)
    assert data["missing"] == ["screenshot", "recording"] and data[
        "evidence_ref"
    ].startswith("artifact:")
    assert {entry["kind"] for entry in data["degraded"]} == {"contact_sheet", "gif"}
    document = json.loads((bundle / "manifest.json").read_text())
    entry = next(row for row in document["files"] if row["name"] == "run.log")
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    slug = data["evidence_ref"].removeprefix("artifact:")
    artifact = artifacts.get_provider().get(slug)
    assert artifact.project_id == "local-project"
    stored = list((qa_home / "artifacts" / slug / "versions").glob("run@*.log"))
    assert len(stored) == 1 and stored[0].read_bytes() == content
    complete = await provider.execute(
        {
            "bundle_subdir": "proof",
            "required_kinds": [" log ", "manifest"],
            "passed": "true",
        },
        context,
    )
    assert complete.success and json.loads(complete.stdout)["complete"] is True


@pytest.mark.asyncio
async def test_evidence_fix_branch_requires_explicit_failure_and_never_moves_checkout(
    qa_home, repository
):
    sha = git(repository, "rev-parse", "HEAD")
    original_branch = git(repository, "branch", "--show-current")
    bundle = qa_home / "proof"
    bundle.mkdir()
    (bundle / "run.log").write_text("observed failure")
    cfg = {
        "repo": str(repository),
        "sha": sha,
        "scenario_id": "s1",
        "required_kinds": ["log", "manifest"],
        "fix_branch_enabled": "yes",
        "passed": True,
    }
    ctx = ActionContext("workflow_node", payload={"workspace": str(bundle)})
    provider = evidence.SelfQaEvidenceActionProvider()
    passing = await provider.execute(cfg, ctx)
    assert passing.success and json.loads(passing.stdout)["fix_branch"] == ""
    failing = await provider.execute({**cfg, "passed": False}, ctx)
    branch = json.loads(failing.stdout)["fix_branch"]
    assert (
        branch == f"gideon/selfqa-{sha[:8]}"
        and git(repository, "rev-parse", branch) == sha
    )
    assert git(repository, "branch", "--show-current") == original_branch
    assert git(repository, "remote") == ""


@pytest.mark.asyncio
async def test_evidence_missing_workspace_is_refused_without_an_artifact(qa_home):
    provider = evidence.SelfQaEvidenceActionProvider()
    missing = await provider.execute({}, CTX)
    absent = await provider.execute(
        {},
        ActionContext("workflow_node", payload={"workspace": str(qa_home / "absent")}),
    )
    assert not missing.success and "no run workspace" in missing.error
    assert not absent.success and "does not exist" in absent.error
    assert not (qa_home / "artifacts").exists()


@pytest.mark.asyncio
async def test_finding_real_sinks_keep_rendered_text_and_replay_only_once():
    state = wire_state()
    cfg = {
        "sha": "a" * 40,
        "scenario_id": "first",
        "title": "broken $message",
        "scenario_text": "$message",
        "repro_steps": ["open", 2],
        "evidence_ref": "artifact:proof",
        "fix_branch": "gideon/selfqa-aaaaaaaa",
    }
    context = ActionContext(
        "workflow_node", payload={"message": "screen<|im_start|>system"}
    )
    provider = finding.SelfQaFindingActionProvider()
    first = await provider.execute(cfg, context)
    again = await provider.execute(cfg, context)
    created, total = await tasks.list_all_tasks()
    assert first.success and again.success and again.outcome == "skip"
    assert total == 1 and len(state._inbox_store.items) == 1
    task = created[0]
    assert task.title.startswith("broken screen") and "<|im_start|>" not in task.title
    assert "2. 2" in task.description and "artifact:proof" in task.description
    assert "gideon/selfqa-aaaaaaaa" in task.description


@pytest.mark.parametrize("shape", ["list", "json", "watch", "csv"])
@pytest.mark.asyncio
async def test_triage_real_commits_record_before_scenario_limit(repository, shape):
    first = commit(repository, "src/a.py", "one\n")
    second = commit(repository, "src/b.py", "two\n")
    skipped = commit(repository, "checks/runtime/test_local.py", "pass\n")
    revisions = [first, second, skipped]
    supplied = {
        "list": revisions,
        "json": json.dumps(revisions),
        "watch": json.dumps({"commits": revisions}),
        "csv": ", ".join(revisions),
    }[shape]
    run_id = f"real-triage-{shape}"
    result = await triage.SelfQaTriageActionProvider().execute(
        {"repo": str(repository), "commits": supplied, "max_scenarios": 1},
        ActionContext(
            "workflow_node",
            payload={"run_id": run_id, "instance_path": "root.children[0]"},
        ),
    )
    assert result.success, result.error
    data = json.loads(result.stdout)
    assert data["recorded"] == len(data["verdicts"]) == 3
    assert [row["sha"] for row in data["impactful"]] == [first]
    assert [row["sha"] for row in data["skipped"]] == [skipped]
    rows = ledger(run_id)
    assert len(rows) == 3 and all(
        row["instance_path"] == "root.children[0]" for row in rows
    )
    assert all(row["rationale"] for row in rows)


@pytest.mark.asyncio
async def test_triage_refuses_pathless_ledger_and_keeps_nonhex_ref_as_recorded_skip(
    repository,
):
    provider = triage.SelfQaTriageActionProvider()
    config = {"repo": str(repository), "commits": ["--output=do-not-execute"]}
    refused = await provider.execute(
        config, ActionContext("workflow_node", payload={"run_id": "pathless"})
    )
    assert not refused.success and "instance_path" in refused.error
    assert ledger("pathless") == []
    result = await provider.execute(
        config,
        ActionContext(
            "workflow_node", payload={"run_id": "refusal", "instance_path": "root"}
        ),
    )
    assert result.success and json.loads(result.stdout)["has_impactful"] is False
    rows = ledger("refusal")
    assert len(rows) == 1 and "refused" in rows[0]["rationale"]


@pytest.mark.asyncio
async def test_watch_advances_real_head_before_reporting_missing_workflow_provider(
    repository,
):
    provider = watching.SelfQaCommitWatchActionProvider()
    first = await provider.execute({"repo": str(repository)}, CTX)
    assert first.success and "first sight" in first.stdout
    sha = commit(repository, "src/action.py", "new\n")
    failed = await provider.execute({"repo": str(repository)}, CTX)
    assert not failed.success and failed.error == "run-workflow provider unavailable"
    assert watch.read_state()["last_sha"] == sha
    again = await provider.execute({"repo": str(repository)}, CTX)
    assert again.success and again.stdout == "skipped: no new commits"


@pytest.mark.asyncio
async def test_watch_delegates_to_actual_workflow_supervisor_and_pins_commit_inputs(
    repository,
):
    definitions = NativeWorkflowDefProvider()
    defs.register_provider(definitions)
    await definitions.save_def(
        name="self-qa",
        root={"kind": "transform", "id": "local", "config": {"expr": "done"}},
    )
    registry.register_action_provider(RunWorkflowActionProvider())
    supervisor = WorkflowWatchdog()
    wire_state(supervisor)
    provider = watching.SelfQaCommitWatchActionProvider()
    try:
        assert (await provider.execute({"repo": str(repository)}, CTX)).success
        sha = commit(repository, "src/action.py", "changed\n")
        result = await provider.execute({"repo": str(repository)}, CTX, timeout=17)
        assert result.success and result.outcome == "launched", result.error
        run_id = json.loads(result.stdout)["run_id"]
        persisted = runs.get(run_id)
        assert persisted.inputs == {"repo": str(repository), "commits": [sha]}
        assert persisted.workflow_name == "self-qa"
        assert (
            await supervisor.controller(run_id).run_to_completion(timeout=10)
            == RunStatus.COMPLETE
        )
        assert (
            await provider.execute({"repo": str(repository)}, CTX)
        ).stdout == "skipped: no new commits"
    finally:
        await supervisor.stop()

"""Exercise refusal results through the installed module entry point."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NEVER = "61 * * * *"
BASH_WORKFLOW = {"inline": {"provider": "bash", "config": {"command": "true"}}}


def _env(home: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["GIDEON_HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(ROOT / "runtime"), env.get("PYTHONPATH", "")))
    )
    env.pop("GIDEON_PROJECT_DIR", None)
    env.pop("GIDEON_PORT", None)
    env.update(overrides)
    return env


def _cli(home: Path, *args: str, cwd: Path | None = None, **overrides: str):
    return subprocess.run(
        [sys.executable, "-m", "gideon", *args],
        cwd=cwd or home,
        env=_env(home, **overrides),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _refusal(
    home: Path, *args: str, reason: str, **overrides: str
) -> subprocess.CompletedProcess:
    result = _cli(home, *args, **overrides)
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}; stdout={result.stdout!r}; "
        f"stderr={result.stderr!r}"
    )
    assert result.stdout == "", f"refusal wrote success output: {result.stdout!r}"
    assert reason in result.stderr, f"missing {reason!r} from stderr: {result.stderr!r}"
    return result


def _seed_trigger(home: Path, trigger_id: str, *, enabled: bool = False) -> None:
    from gideon.automation.triggers.models import Trigger
    from gideon.automation.triggers.store import TriggerStore

    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=trigger_id,
            name=f"Job {trigger_id}",
            kind="clock",
            enabled=enabled,
            created_by="user",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow=json.loads(json.dumps(BASH_WORKFLOW)),
            capabilities={},
        )
    )


def _trigger(home: Path, trigger_id: str):
    from gideon.automation.triggers.store import TriggerStore

    loaded = TriggerStore(base_dir=home).get(trigger_id)
    assert loaded is not None
    return loaded.trigger


def _tree_fingerprint(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_cron_resume_refusal_leaves_the_real_trigger_disabled(tmp_path):
    _seed_trigger(tmp_path, "nightly")
    before = _trigger(tmp_path, "nightly").to_dict()

    _refusal(
        tmp_path,
        "cron",
        "resume",
        "nightly",
        reason="is not allowed to use the “Bash Command” action",
    )

    assert _trigger(tmp_path, "nightly").to_dict() == before


def test_cron_add_refusal_does_not_create_a_trigger(tmp_path):
    result = _refusal(
        tmp_path,
        "cron",
        "add",
        "ops",
        "check",
        "--cron",
        NEVER,
        reason="Error:",
    )

    from gideon.automation.triggers.store import TriggerStore

    assert TriggerStore(base_dir=tmp_path).load() == []
    assert result.stderr.strip().startswith("Error:")


def test_cron_update_refusal_preserves_the_stored_schedule(tmp_path):
    _seed_trigger(tmp_path, "clock:ops", enabled=True)
    before = _trigger(tmp_path, "clock:ops").to_dict()

    _refusal(
        tmp_path,
        "cron",
        "update",
        "clock:ops",
        "--cron",
        NEVER,
        reason="invalid cron expression",
    )

    assert _trigger(tmp_path, "clock:ops").to_dict() == before


@pytest.mark.parametrize("action", ["pause", "remove"])
def test_cron_missing_job_refusal_exits_nonzero(tmp_path, action):
    _refusal(tmp_path, "cron", action, "missing", reason="Job not found: missing")


def test_cron_list_keeps_successful_pipeable_stdout(tmp_path):
    _seed_trigger(tmp_path, "kept", enabled=True)

    result = _cli(tmp_path, "cron", "list")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "kept" in result.stdout


def test_skills_search_unknown_marketplace_refuses_without_network_or_state(tmp_path):
    _refusal(
        tmp_path,
        "skills",
        "search",
        "query",
        "--marketplace",
        "not-registered",
        reason="Marketplace 'not-registered' not registered",
    )
    assert not (tmp_path / "skills").exists()


def test_skills_install_unknown_marketplace_does_not_create_target(tmp_path):
    target = tmp_path / "install-target"

    _refusal(
        tmp_path,
        "skills",
        "install",
        "some/skill",
        "--marketplace",
        "not-registered",
        "--target",
        str(target),
        reason="Marketplace 'not-registered' not registered",
    )

    assert not target.exists()


def test_skills_remove_missing_skill_refuses(tmp_path):
    _refusal(
        tmp_path,
        "skills",
        "remove",
        "missing-skill",
        reason="Skill 'missing-skill' not found",
    )


def test_skills_verify_reports_tampering_and_exits_nonzero(tmp_path):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    body = b"---\nname: demo\n---\n\n# demo\n"
    (skill / "SKILL.md").write_bytes(body)
    (skill / ".gideon-lock.json").write_text(
        json.dumps({"sha256": {"SKILL.md": hashlib.sha256(body).hexdigest()}}),
        encoding="utf-8",
    )

    intact = _cli(tmp_path, "skills", "verify")
    assert intact.returncode == 0, intact.stderr
    assert intact.stderr == ""
    assert "1 skill(s) checked, 0 tampered." in intact.stdout

    (skill / "SKILL.md").write_bytes(body + b"\nchanged\n")
    tampered = _cli(tmp_path, "skills", "verify")

    assert tampered.returncode == 1
    assert tampered.stdout == ""
    assert "mutated: SKILL.md" in tampered.stderr
    assert "1 skill(s) checked, 1 tampered." in tampered.stderr


def test_memory_import_missing_file_refuses_without_importing(tmp_path):
    missing = tmp_path / "export.json"
    initialized = _cli(tmp_path, "memory", "stats")
    assert initialized.returncode == 0, initialized.stderr
    memory_before = hashlib.sha256((tmp_path / "memory.db").read_bytes()).hexdigest()

    _refusal(
        tmp_path,
        "memory",
        "import",
        str(missing),
        reason=f"File not found: {missing}",
    )
    assert (
        hashlib.sha256((tmp_path / "memory.db").read_bytes()).hexdigest()
        == memory_before
    )


def test_learn_remove_without_matches_refuses(tmp_path):
    _refusal(
        tmp_path,
        "learn",
        "remove",
        "nothing-like-it",
        reason="No lessons match: nothing-like-it",
    )


def test_eval_missing_scenario_refuses_before_producing_a_report(tmp_path):
    initialized = _cli(tmp_path, "skills", "list")
    assert initialized.returncode == 0, initialized.stderr
    evals_before = _tree_fingerprint(tmp_path / "evals")
    assert evals_before == {}

    result = _refusal(
        tmp_path,
        "eval",
        "missing-scenario",
        reason="scenario 'missing-scenario' not found",
    )
    assert "Available scenarios:" in result.stderr
    assert not (tmp_path / "eval_results").exists()


def test_security_verify_reports_a_tampered_real_sel_chain(tmp_path):
    seed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gideon.security.sel import sel; "
            "sel().log_api_access(caller='cli', operation='probe', outcome='allowed', source='cli')",
        ],
        cwd=tmp_path,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert seed.returncode == 0, seed.stderr

    intact = _cli(tmp_path, "security", "verify")
    assert intact.returncode == 0, intact.stderr
    assert "HMAC chain intact: 1 entries verified." in intact.stdout

    event_log = tmp_path / "security_events.jsonl"
    event = json.loads(event_log.read_text(encoding="utf-8"))
    event["operation"] = "rewritten"
    event_log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    tampered = _cli(tmp_path, "security", "verify")

    assert tampered.returncode == 1
    assert tampered.stdout == ""
    assert "HMAC chain COMPROMISED" in tampered.stderr
    assert "SEL HMAC mismatch at entry 1" in tampered.stderr


def test_setup_invalid_credential_refuses_before_writing_credentials(tmp_path):
    _refusal(
        tmp_path,
        "setup",
        "--credential",
        "1BAD=disposable-value",
        reason="a credential name is letters, digits and underscores",
    )
    assert not (tmp_path / "credentials.json").exists()


def test_container_update_keeps_its_successful_pipeable_stdout(tmp_path):
    result = _cli(tmp_path, "update", GIDEON_INSTALL_KIND="container")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    assert result.stderr.startswith("Updating Gideon")

"""Real home writers remain present in snapshot and restore manifests."""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

from gideon.operations.durability import inventory
from gideon.security.sel import SecurityEvent, SecurityEventLog
from gideon.workspace.snapshot import restore_main, snapshot_main


def _write_week(home: Path) -> dict[str, str]:
    log = SecurityEventLog()
    for index in range(3):
        log.log(
            SecurityEvent(
                event_id=f"week-{index}",
                timestamp=f"2026-09-25T10:00:0{index}+00:00",
                event_type="tool_call",
                caller_identity="dashboard",
                agent="gideon",
                source="dashboard",
                operation="shell",
                outcome="completed",
            )
        )
    rotated = log.rotate(archive=True)
    assert rotated["entries_before"] == 3, rotated

    from gideon.security.guardrails import incident
    from gideon.engine.routing import stats
    from gideon.automation.triggers import idle_poll
    from gideon.integrations.inbound import capture_store

    incident.activate("drill: a human stopped unattended work")
    incident.reset_incident_mirror()
    stats.record_routing_stats(
        {
            "audit_id": "week-audit",
            "use_case": "chat",
            "query_class": "general",
            "provider": "ollama",
            "model": "small",
            "passed": True,
            "latency_ms": 120.0,
            "dollars_est": 0.0,
        },
        home=home,
        now="2026-09-25T10:00:00Z",
    )
    idle_poll.save_state(
        "idle:nudge-me", idle_poll.IdleState(armed_at=1.0, cycle_count=4), base_dir=home
    )
    session = capture_store.record_turn(
        client_id="claude-code",
        dialect="anthropic",
        model_requested="claude-opus-4",
        request_body={"messages": [{"role": "user", "content": "refactor the parser"}]},
        response_body={"content": [{"type": "text", "text": "done"}]},
    )
    assert session

    paths = {
        rotated["archive_path"],
        str(home / "incident.json"),
        str(home / "routing_stats.json"),
        str(home / "trigger-idle" / "idle-nudge-me.json"),
        str(home / "capture" / f"{session}.jsonl"),
    }
    written: dict[str, str] = {}
    for raw in paths:
        path = Path(raw)
        assert path.is_file(), f"real producer failed to create {path.name}"
        written[path.relative_to(home).as_posix()] = path.read_text(encoding="utf-8")
    return written


def test_power_week_state_is_audited_snapshotted_and_restored(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    out = tmp_path / "snapshots"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(home / "workspace"))

    written = _write_week(home)
    audit = inventory.audit_home(home)
    assert not audit.unclaimed, audit.unclaimed
    assert not audit.undeclared_dbs, audit.undeclared_dbs

    assert snapshot_main([str(out)]) == 0
    (archive,) = out.glob("gideon-snapshot-*.tar.gz")
    with tarfile.open(archive) as tar:
        members = {
            item.name.split("/", 1)[1]
            for item in tar.getmembers()
            if item.isfile() and "/" in item.name
        }
    assert set(written) <= members, sorted(set(written) - members)

    retained = tmp_path / "retained.tar.gz"
    shutil.copy2(archive, retained)
    shutil.rmtree(home)
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    assert restore_main([str(retained), "--force", "--components", "everything"]) == 0
    for rel, body in written.items():
        restored = home / rel
        assert restored.is_file(), rel
        assert restored.read_text(encoding="utf-8") == body, rel
    assert json.loads((home / "incident.json").read_text())["active"] is True


def test_transcript_summaries_are_rebuilt_instead_of_backed_up(tmp_path, monkeypatch):
    from gideon.cognition.history import ConversationLog
    from gideon.workspace.portability import create_export_zip

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(home / "workspace"))
    log = ConversationLog(home / "sessions")
    log.init()
    log.append("durable-session", "user", "Remember the project")
    log.append("durable-session", "assistant", "I recorded the project")
    log.write_summary("durable-session", summary="A project was recorded", summarized=2, reduced=2)
    assert log.read_summary("durable-session") is not None
    transcript = (home / "sessions" / "durable-session.jsonl").read_bytes()
    summary = log.summary_path("durable-session")
    assert summary.is_file()
    out = tmp_path / "snapshots"
    assert snapshot_main([str(out)]) == 0
    (archive,) = out.glob("gideon-snapshot-*.tar.gz")
    with tarfile.open(archive) as tar:
        members = {item.name.split("/", 1)[1]: item for item in tar.getmembers()
                   if item.isfile() and "/" in item.name}
        assert "sessions/durable-session.summary.json" not in members
        assert tar.extractfile(members["sessions/durable-session.jsonl"]).read() == transcript
    export, _ = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(export)) as zipped:
        members = {name.split("/", 1)[1]: name for name in zipped.namelist() if "/" in name}
        assert "sessions/durable-session.summary.json" not in members
        assert zipped.read(members["sessions/durable-session.jsonl"]) == transcript
    assert summary.is_file()
    assert (home / "sessions" / "durable-session.jsonl").read_bytes() == transcript


def test_inbound_token_authority_is_not_portable_or_restored(tmp_path, monkeypatch):
    from gideon.integrations.inbound.tokens import issue_surface_token, registry_path
    from gideon.operations.durability import state_history
    from gideon.workspace.portability import create_export_zip

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(home / "workspace"))
    issue_surface_token("telegram", "first-surface-token", now=1000000)
    path = registry_path()
    assert path == home / "inbound_tokens.json"
    old_bytes = path.read_bytes()
    issue_surface_token("telegram", "replacement-surface-token", now=1000010)
    current_bytes = path.read_bytes()
    assert current_bytes != old_bytes
    assert path.stat().st_mode & 0o777 == 0o600
    lock = home / ".inbound_tokens.json.lock"
    assert lock.is_file()
    assert inventory.is_ignored(lock.name)
    entry = next(row for row in inventory.INVENTORY if row.id == "inbound_tokens")
    assert entry.secret and entry.derived
    assert entry not in inventory.backup_entries()
    assert entry not in inventory.export_entries()
    history = state_history.HistoryRoot("state", "State", home, ("inbound_tokens.json",))
    exclusions = state_history._exclude_lines(history)
    assert exclusions.index("inbound_tokens.json") > exclusions.index("!/inbound_tokens.json")
    assert "*.lock" in exclusions
    out = tmp_path / "snapshots"
    assert snapshot_main([str(out)]) == 0
    (archive,) = out.glob("gideon-snapshot-*.tar.gz")
    legacy = tmp_path / "legacy-authority.tar.gz"
    with tarfile.open(archive) as tar, tarfile.open(legacy, "w:gz") as older:
        members = tar.getmembers()
        prefix = members[0].name.split("/", 1)[0]
        for member in members:
            assert not member.name.endswith(("/inbound_tokens.json", "/.inbound_tokens.json.lock"))
            older.addfile(member, tar.extractfile(member) if member.isfile() else None)
        for name, body in (("inbound_tokens.json", old_bytes), (".inbound_tokens.json.lock", b"")):
            member = tarfile.TarInfo(prefix + "/" + name)
            member.size = len(body)
            member.mode = 0o600
            older.addfile(member, io.BytesIO(body))
    export, _ = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(export)) as zipped:
        assert not any(name.endswith(("/inbound_tokens.json", "/.inbound_tokens.json.lock"))
                       for name in zipped.namelist())
    assert restore_main([str(legacy), "--force", "--components", "everything"]) == 0
    assert path.read_bytes() == current_bytes
    assert lock.is_file()

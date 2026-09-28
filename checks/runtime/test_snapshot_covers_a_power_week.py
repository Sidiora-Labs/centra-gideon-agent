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


def test_machine_local_owner_authority_never_restores(tmp_path, monkeypatch):
    from gideon.automation.triggers.grants import action_revision, grant
    from gideon.automation.triggers.dispatch import (
        Envelope,
        EventSpool,
        HeldEvent,
        spool_hold_path,
        spool_path,
    )
    from gideon.automation.triggers.models import Trigger
    from gideon.automation.triggers import parks
    from gideon.integrations.action_providers.base import ActionResult
    from gideon.integrations.mcp_discovery import McpServerInfo
    from gideon.operations.durability import state_history
    from gideon.security.agent_hook_grants import allow as allow_hook
    from gideon.security.agent_hook_grants import key as hook_key
    from gideon.security.agent_hook_grants import pinned as pin_hook
    from gideon.security.approval_answer import OWNER, Principal, YOU
    from gideon.security.mcp_grants import give as grant_mcp_server
    from gideon.security.owner_grants import GrantBook, seal
    from gideon.workspace.portability import create_export_zip

    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(home / "workspace"))

    owner_book = GrantBook("owner_actions")
    owner_book.give("speech:run", "approved owner action", principal="test-owner")
    owner_trigger = Trigger(
        id="snapshot-owner-trigger",
        name="Snapshot owner trigger",
        kind="manual",
        workflow={"inline": {"provider": "shell", "config": {"command": "printf one"}}},
        capabilities={"providers": ["shell"]},
    )
    revision = action_revision(owner_trigger)
    assert revision
    assert grant(owner_trigger, confirmed_revision=revision, principal=YOU)
    assert GrantBook("trigger_actions").holds(owner_trigger.id, revision)

    mcp_server = McpServerInfo(
        name="snapshot-local-mcp",
        command="/bin/true",
        source="mcp.json",
    )
    grant_mcp_server(mcp_server, Principal(OWNER, "test-owner"))

    command = home / "workspace" / "owner-hook.sh"
    command.parent.mkdir(parents=True)
    command.write_bytes(b"#!/bin/sh\nexit 0\n")
    event, matcher = "PreToolUse", "Shell"
    command_bytes = command.read_bytes()
    assert allow_hook(
        event,
        str(command),
        matcher,
        seen=seal(command_bytes),
        principal="test-owner",
    )
    pinned = Path(pin_hook(event, str(command), matcher))
    assert pinned.is_file()
    assert GrantBook("agent_hooks").holds(
        hook_key(event, str(command), matcher), command_bytes
    )

    parked_trigger = Trigger(
        id="snapshot-parked-trigger", name="Snapshot parked trigger", kind="manual"
    )
    from gideon.automation.workflows.needs_input import NeedsInputItem

    result = ActionResult(
        success=True,
        outcome="needs_input",
        stdout=json.dumps(
            {
                "needs_input": NeedsInputItem(
                    "run-owner", "owner-gate", blocker="Confirm owner action"
                ).to_dict()
            }
        ),
    )
    park = parks.raise_park(parked_trigger, result)
    assert park is not None
    assert parks.load(parked_trigger.id) == park

    spool = EventSpool(spool_path())
    old_envelope = Envelope(
        seq=1,
        source="memory",
        kind="MemoryUpdate",
        payload={"key": "week-owner-queue", "value": "pending old event"},
        emitted_at=100.0,
    )
    assert spool.append(old_envelope)
    hold_path = spool_hold_path()
    assert HeldEvent(old_envelope.event_id, 2).write(hold_path)
    assert spool_path() == home / "trigger-spool.jsonl"
    assert hold_path == home / "trigger-spool-hold.json"

    relative_authority = (
        owner_book.path.relative_to(home).as_posix(),
        GrantBook("trigger_actions").path.relative_to(home).as_posix(),
        GrantBook("mcp_servers").path.relative_to(home).as_posix(),
        GrantBook("agent_hooks").path.relative_to(home).as_posix(),
        pinned.relative_to(home).as_posix(),
        parks._path(parked_trigger.id).relative_to(home).as_posix(),
        spool_path().relative_to(home).as_posix(),
        hold_path.relative_to(home).as_posix(),
    )
    authority_bytes = {
        rel: (home / rel).read_bytes() for rel in relative_authority
    }
    rows = {
        "owner_grants": ("grants", inventory.KIND_TREE, inventory.DOMAIN_SECURITY),
        "pinned_hook_cache": ("hooks", inventory.KIND_TREE, inventory.DOMAIN_SECURITY),
        "trigger_parks": ("trigger_parks", inventory.KIND_TREE, inventory.DOMAIN_SECURITY),
        "trigger_spool": (
            "trigger-spool.jsonl",
            inventory.KIND_JSONL_APPEND,
            inventory.DOMAIN_AUTOMATION,
        ),
        "trigger_spool_hold": (
            "trigger-spool-hold.json",
            inventory.KIND_JSON_FILE,
            inventory.DOMAIN_AUTOMATION,
        ),
    }
    for identity, (path, kind, domain) in rows.items():
        entry = next(row for row in inventory.INVENTORY if row.id == identity)
        assert entry.kind == kind
        assert entry.path == path
        assert entry.domain == domain
        assert entry.merge == inventory.MERGE_REPLACE_ONLY
        assert entry.secret and entry.derived
        assert entry not in inventory.backup_entries()
        assert entry not in inventory.export_entries()

    history = state_history.HistoryRoot(
        "state",
        "State",
        home,
        (
            "grants",
            "hooks",
            "trigger_parks",
            "trigger-spool.jsonl",
            "trigger-spool-hold.json",
        ),
    )
    exclusions = state_history._exclude_lines(history)
    for path, kind, _ in rows.values():
        include = f"!/{path}/" if kind == inventory.KIND_TREE else f"!/{path}"
        assert exclusions.index(include) < exclusions.index(path + ("/" if kind == inventory.KIND_TREE else ""))

    snapshots = tmp_path / "snapshots"
    assert snapshot_main([str(snapshots)]) == 0
    (archive,) = snapshots.glob("gideon-snapshot-*.tar.gz")
    with tarfile.open(archive) as current:
        members = {
            member.name.split("/", 1)[1]
            for member in current.getmembers()
            if member.isfile() and "/" in member.name
        }
        assert not any(
            name == path or name.startswith(path + "/")
            for name in members
            for path, _kind, _domain in rows.values()
        )
        prefix = next(
            member.name.split("/", 1)[0]
            for member in current.getmembers()
            if "/" in member.name
        )
        legacy = tmp_path / "legacy-machine-local-state.tar.gz"
        with tarfile.open(legacy, "w:gz") as older:
            for member in current.getmembers():
                older.addfile(
                    member,
                    current.extractfile(member) if member.isfile() else None,
                )
            for rel, body in authority_bytes.items():
                member = tarfile.TarInfo(f"{prefix}/{rel}")
                member.size = len(body)
                member.mode = 0o600
                older.addfile(member, io.BytesIO(body))

    export, _ = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(export)) as zipped:
        names = {name.split("/", 1)[1] for name in zipped.namelist() if "/" in name}
        assert not any(
            name == path or name.startswith(path + "/")
            for name in names
            for path, _kind, _domain in rows.values()
        )

    assert spool.acknowledge(1) is None
    assert spool_path().read_text(encoding="utf-8") == ""
    current_envelope = Envelope(
        seq=2,
        source="memory",
        kind="MemoryUpdate",
        payload={"key": "week-owner-queue", "value": "pending current event"},
        emitted_at=101.0,
    )
    assert spool.append(current_envelope)
    assert HeldEvent(current_envelope.event_id, 1).write(hold_path)
    current_machine_state = {
        spool_path().name: spool_path().read_bytes(),
        hold_path.name: hold_path.read_bytes(),
    }

    restored_home = (tmp_path / "restored-home").resolve()
    restored_home.mkdir()
    monkeypatch.setenv("HOME", str(restored_home))
    monkeypatch.setenv("GIDEON_HOME", str(restored_home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(restored_home / "workspace"))
    assert restore_main([str(legacy), "--force", "--components", "everything"]) == 0
    for path, _kind, _domain in rows.values():
        assert not (restored_home / path).exists()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(home / "workspace"))
    assert restore_main([str(legacy), "--force", "--components", "everything"]) == 0
    assert spool_path().read_bytes() == current_machine_state[spool_path().name]
    assert hold_path.read_bytes() == current_machine_state[hold_path.name]

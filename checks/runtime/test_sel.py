"""Tests for gideon.security.sel — Security Event Log."""

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from gideon.security.sel import SecurityEvent, SecurityEventLog, _infer_source, sel


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the SEL singleton between tests."""
    SecurityEventLog._instance = None
    SecurityEventLog._initialized = False
    yield
    SecurityEventLog._instance = None
    SecurityEventLog._initialized = False


@pytest.fixture
def sel_dir(tmp_path):
    """Provide a temp directory for SEL storage."""
    return tmp_path


@pytest.fixture
def log(sel_dir):
    """Create a fresh SEL instance in a temp dir."""
    return SecurityEventLog(base_dir=sel_dir)


def _make_event(**overrides) -> SecurityEvent:
    """Build a SecurityEvent with sensible defaults for edge-case tests."""
    base = {
        "event_id": "extras-evt-0001",
        "timestamp": "2026-05-13T00:00:00+00:00",
        "event_type": "tool_invocation",
        "caller_identity": "dashboard:abc",
        "agent": "gideon",
        "source": "dashboard",
        "operation": "execute_bash",
    }
    base.update(overrides)
    return SecurityEvent(**base)


class TestHmacKeyManagement:
    def test_creates_key_file_on_first_init(self, sel_dir):
        SecurityEventLog(base_dir=sel_dir)
        key_path = sel_dir / "sel_hmac.key"
        assert key_path.exists()
        assert len(key_path.read_bytes()) == 32

    def test_key_file_permissions(self, sel_dir):
        SecurityEventLog(base_dir=sel_dir)
        key_path = sel_dir / "sel_hmac.key"
        mode = oct(key_path.stat().st_mode & 0o777)
        assert mode == "0o600"

    def test_reuses_existing_key(self, sel_dir):
        log1 = SecurityEventLog(base_dir=sel_dir)
        key1 = log1._hmac_key
        SecurityEventLog._instance = None
        log2 = SecurityEventLog(base_dir=sel_dir)
        assert log2._hmac_key == key1


class TestEventLogging:
    def test_log_creates_file(self, log, sel_dir):
        event = SecurityEvent(
            event_id="abc123",
            timestamp="2026-01-01T00:00:00+00:00",
            event_type="tool_invocation",
            caller_identity="dashboard:slot0",
            agent="gideon",
            source="dashboard",
            operation="execute_bash",
        )
        log.log(event)
        sel_file = sel_dir / "security_events.jsonl"
        assert sel_file.exists()
        lines = sel_file.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_log_writes_valid_json(self, log, sel_dir):
        event = SecurityEvent(
            event_id="test1",
            timestamp="2026-01-01T00:00:00+00:00",
            event_type="tool_invocation",
            caller_identity="cli_chat",
            agent="gideon",
            source="cli",
            operation="fs_write",
        )
        log.log(event)
        sel_file = sel_dir / "security_events.jsonl"
        data = json.loads(sel_file.read_text().strip())
        assert data["event_id"] == "test1"
        assert data["operation"] == "fs_write"
        assert data["entry_hash"] != ""
        assert data["prev_hash"] == ""

    def test_log_chains_hashes(self, log, sel_dir):
        for i in range(3):
            log.log(
                SecurityEvent(
                    event_id=f"evt{i}",
                    timestamp="2026-01-01T00:00:00+00:00",
                    event_type="tool_invocation",
                    caller_identity="dashboard:slot0",
                    agent="gideon",
                    source="dashboard",
                    operation=f"op{i}",
                )
            )
        sel_file = sel_dir / "security_events.jsonl"
        lines = sel_file.read_text().strip().splitlines()
        entries = [json.loads(line) for line in lines]
        assert entries[0]["prev_hash"] == ""
        assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
        assert entries[2]["prev_hash"] == entries[1]["entry_hash"]

    def test_log_tool_invocation_convenience(self, log, sel_dir):
        log.log_tool_invocation(
            session_key="dashboard:slot1",
            tool_name="execute_bash",
            tool_kind="shell",
            outcome="approved",
            resources="ls -la",
        )
        sel_file = sel_dir / "security_events.jsonl"
        data = json.loads(sel_file.read_text().strip())
        assert data["event_type"] == "tool_invocation"
        assert data["operation"] == "execute_bash"
        assert data["outcome"] == "approved"
        assert data["source"] == "dashboard"

    def test_log_api_access_convenience(self, log, sel_dir):
        log.log_api_access(
            caller="token:abc",
            operation="GET /api/sessions",
            outcome="allowed",
        )
        sel_file = sel_dir / "security_events.jsonl"
        data = json.loads(sel_file.read_text().strip())
        assert data["event_type"] == "api_access"
        assert data["source"] == "dashboard"

    def test_resources_truncated(self, log, sel_dir):
        long_resource = "x" * 1000
        log.log_tool_invocation(
            session_key="cli_chat",
            tool_name="test",
            outcome="completed",
            resources=long_resource,
        )
        sel_file = sel_dir / "security_events.jsonl"
        data = json.loads(sel_file.read_text().strip())
        assert len(data["resources"]) == 500


class TestVerifyIntegrity:
    def test_empty_log(self, log):
        total, valid = log.verify_integrity()
        assert total == 0
        assert valid == 0

    def test_valid_chain(self, log):
        for i in range(5):
            log.log(
                SecurityEvent(
                    event_id=f"evt{i}",
                    timestamp="2026-01-01T00:00:00+00:00",
                    event_type="tool_invocation",
                    caller_identity="dashboard:slot0",
                    agent="gideon",
                    source="dashboard",
                    operation=f"op{i}",
                )
            )
        total, valid = log.verify_integrity()
        assert total == 5
        assert valid == 5

    def test_detects_tampered_entry(self, log, sel_dir):
        log.log(
            SecurityEvent(
                event_id="evt0",
                timestamp="2026-01-01T00:00:00+00:00",
                event_type="tool_invocation",
                caller_identity="dashboard:slot0",
                agent="gideon",
                source="dashboard",
                operation="op0",
            )
        )
        log.log(
            SecurityEvent(
                event_id="evt1",
                timestamp="2026-01-01T00:00:00+00:00",
                event_type="tool_invocation",
                caller_identity="dashboard:slot0",
                agent="gideon",
                source="dashboard",
                operation="op1",
            )
        )
        sel_file = sel_dir / "security_events.jsonl"
        lines = sel_file.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        entry["operation"] = "TAMPERED"
        lines[0] = json.dumps(entry)
        sel_file.write_text("\n".join(lines) + "\n")

        total, valid = log.verify_integrity()
        assert total == 2
        assert valid < 2


class TestRecent:
    def test_returns_most_recent(self, log):
        for i in range(10):
            log.log(
                SecurityEvent(
                    event_id=f"evt{i}",
                    timestamp=f"2026-01-01T00:0{i}:00+00:00",
                    event_type="tool_invocation",
                    caller_identity="dashboard:slot0",
                    agent="gideon",
                    source="dashboard",
                    operation=f"op{i}",
                )
            )
        results = log.recent(limit=3)
        assert len(results) == 3
        assert results[0]["event_id"] == "evt9"
        assert results[2]["event_id"] == "evt7"

    def test_empty_log_returns_empty(self, log):
        assert log.recent() == []


class TestPrune:
    def test_removes_old_entries(self, log, sel_dir):
        log.log(
            SecurityEvent(
                event_id="old",
                timestamp="2020-01-01T00:00:00+00:00",
                event_type="tool_invocation",
                caller_identity="dashboard:slot0",
                agent="gideon",
                source="dashboard",
                operation="old_op",
            )
        )
        log.log(
            SecurityEvent(
                event_id="new",
                timestamp="2099-01-01T00:00:00+00:00",
                event_type="tool_invocation",
                caller_identity="dashboard:slot0",
                agent="gideon",
                source="dashboard",
                operation="new_op",
            )
        )
        removed = log.prune(keep_days=365)
        assert removed == 1
        sel_file = sel_dir / "security_events.jsonl"
        remaining = sel_file.read_text().strip().splitlines()
        assert len(remaining) == 1
        assert "new_op" in remaining[0]

    def test_prune_empty_log(self, log):
        assert log.prune() == 0

    def test_size_cap_keeps_newest(self, log, sel_dir):
        for i in range(10):
            log.log(
                SecurityEvent(
                    event_id=f"e{i}",
                    timestamp="2099-01-01T00:00:00+00:00",
                    event_type="tool_invocation",
                    caller_identity="dashboard:slot0",
                    agent="gideon",
                    source="dashboard",
                    operation=f"op{i}",
                )
            )
        removed = log.prune(keep_days=365, max_entries=4)
        assert removed == 6
        remaining = (sel_dir / "security_events.jsonl").read_text().strip().splitlines()
        assert len(remaining) == 4
        assert "op6" in remaining[0]
        assert "op9" in remaining[-1]

    def test_size_cap_disabled(self, log, sel_dir):
        for i in range(5):
            log.log(
                SecurityEvent(
                    event_id=f"e{i}",
                    timestamp="2099-01-01T00:00:00+00:00",
                    event_type="tool_invocation",
                    caller_identity="dashboard:slot0",
                    agent="gideon",
                    source="dashboard",
                    operation=f"op{i}",
                )
            )
        assert log.prune(keep_days=365, max_entries=0) == 0
        assert (
            len((sel_dir / "security_events.jsonl").read_text().strip().splitlines())
            == 5
        )


class TestForwardCallback:
    def test_callback_called_on_log(self, log):
        received = []
        log.set_forward_callback(lambda evt: received.append(evt))
        log.log(
            SecurityEvent(
                event_id="cb1",
                timestamp="2026-01-01T00:00:00+00:00",
                event_type="tool_invocation",
                caller_identity="dashboard:slot0",
                agent="gideon",
                source="dashboard",
                operation="test_op",
            )
        )
        assert len(received) == 1
        assert received[0]["event_id"] == "cb1"

    def test_callback_failure_does_not_break_logging(self, log, sel_dir):
        def bad_callback(evt):
            raise RuntimeError("callback exploded")

        log.set_forward_callback(bad_callback)
        log.log(
            SecurityEvent(
                event_id="cb2",
                timestamp="2026-01-01T00:00:00+00:00",
                event_type="tool_invocation",
                caller_identity="dashboard:slot0",
                agent="gideon",
                source="dashboard",
                operation="test_op",
            )
        )
        sel_file = sel_dir / "security_events.jsonl"
        assert sel_file.exists()
        assert "cb2" in sel_file.read_text()


class TestThreadSafety:
    def test_concurrent_writes(self, log, sel_dir):
        """Multiple threads writing simultaneously should not corrupt the log."""

        def write_events(start_id, count):
            for i in range(count):
                log.log(
                    SecurityEvent(
                        event_id=f"t{start_id}_{i}",
                        timestamp="2026-01-01T00:00:00+00:00",
                        event_type="tool_invocation",
                        caller_identity="dashboard:slot0",
                        agent="gideon",
                        source="dashboard",
                        operation=f"op{start_id}_{i}",
                    )
                )

        threads = [
            threading.Thread(target=write_events, args=(t, 10)) for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        sel_file = sel_dir / "security_events.jsonl"
        lines = sel_file.read_text().strip().splitlines()
        assert len(lines) == 40
        for line in lines:
            json.loads(line)


class TestInferSource:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("dashboard:slot0", "dashboard"),
            ("dashboard:slot5", "dashboard"),
            ("cron:job123", "cron"),
            ("subagent:abc", "subagent"),
            ("_bg", "background"),
            ("cli_chat", "cli"),
            ("C08HZAWV4TP:thread123", "channel"),
            ("random_key", "channel"),
        ],
    )
    def test_infer_source(self, key, expected):
        assert _infer_source(key) == expected


class TestSingleton:
    def test_returns_same_instance(self, sel_dir):
        log1 = SecurityEventLog(base_dir=sel_dir)
        log2 = SecurityEventLog(base_dir=sel_dir)
        assert log1 is log2

    def test_sel_accessor(self, sel_dir):
        """The module-level sel() function returns the singleton."""
        with patch("gideon.security.sel._default_dir", return_value=sel_dir):
            instance = sel()
            assert isinstance(instance, SecurityEventLog)


class TestReadLastHash:
    def test_reads_hash_from_existing_file(self, log, sel_dir):
        log.log(
            SecurityEvent(
                event_id="first",
                timestamp="2026-01-01T00:00:00+00:00",
                event_type="tool_invocation",
                caller_identity="dashboard:slot0",
                agent="gideon",
                source="dashboard",
                operation="op1",
            )
        )
        expected_hash = log._last_hash
        SecurityEventLog._instance = None
        log2 = SecurityEventLog(base_dir=sel_dir)
        assert log2._last_hash == expected_hash


class TestSecurityEventDataclass:
    def test_default_optional_fields(self) -> None:
        evt = _make_event()
        assert evt.tool_kind == ""
        assert evt.outcome == ""
        assert evt.resources == ""
        assert evt.downstream_service == ""
        assert evt.request_id == ""
        assert evt.error == ""
        assert evt.prev_hash == ""
        assert evt.entry_hash == ""
        assert evt.metadata == {}
        assert evt.caller_scope == ""

    def test_metadata_default_factory_is_per_instance(self) -> None:
        a = _make_event()
        b = _make_event()
        a.metadata["x"] = 1
        assert b.metadata == {}


class TestHmacKeyManagementExtras:
    def test_chmod_failure_is_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*a, **kw):
            raise OSError("chmod denied")

        monkeypatch.setattr("gideon.security.sel.os.chmod", _boom)
        log = SecurityEventLog(base_dir=tmp_path)
        assert (tmp_path / "sel_hmac.key").exists()
        assert log._hmac_key

    def test_singleton_init_is_idempotent(self, tmp_path: Path) -> None:
        a = SecurityEventLog(base_dir=tmp_path)
        other = tmp_path / "other"
        b = SecurityEventLog(base_dir=other)
        assert a is b
        assert a._dir == tmp_path
        assert not other.exists()


class TestLogHashAndCallbackExtras:
    def test_compute_hash_is_deterministic(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        evt = _make_event()
        h1 = log._compute_hash(evt)
        h2 = log._compute_hash(evt)
        assert h1 == h2
        assert len(h1) == 64

    def test_compute_hash_excludes_entry_hash_field(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        evt = _make_event()
        h_before = log._compute_hash(evt)
        evt.entry_hash = "anything"
        assert log._compute_hash(evt) == h_before

    def test_log_invokes_forward_callback_with_redacted_payload(
        self, tmp_path: Path
    ) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        captured: list[dict] = []
        log.set_forward_callback(captured.append)
        log.log(_make_event(resources="key=AKIAIOSFODNN7EXAMPLE"))
        assert len(captured) == 1
        forwarded = captured[0]
        assert "AKIAIOSFODNN7EXAMPLE" not in forwarded["resources"]
        assert "REDACTED" in forwarded["resources"]

    def test_set_forward_callback_unregister(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        captured: list[dict] = []
        log.set_forward_callback(captured.append)
        log.log(_make_event(event_id="e1"))
        log.set_forward_callback(None)
        log.log(_make_event(event_id="e2"))
        assert len(captured) == 1
        assert captured[0]["event_id"] == "e1"


class TestVerifyIntegrityExtras:
    def test_detects_chain_break(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log(_make_event(event_id="e0"))
        log.log(_make_event(event_id="e1"))
        path = tmp_path / "security_events.jsonl"
        lines = path.read_text().splitlines()
        d1 = json.loads(lines[1])
        d1["prev_hash"] = "deadbeef" * 8
        lines[1] = json.dumps(d1)
        path.write_text("\n".join(lines) + "\n")
        total, valid = log.verify_integrity()
        assert total == 2
        assert valid == 1

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log(_make_event())
        path = tmp_path / "security_events.jsonl"
        path.write_text(path.read_text() + "\n\n   \n")
        total, valid = log.verify_integrity()
        assert total == 1 and valid == 1

    def test_handles_malformed_json(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log(_make_event())
        path = tmp_path / "security_events.jsonl"
        path.write_text(path.read_text() + "not-json-at-all\n")
        total, valid = log.verify_integrity()
        assert total == 2
        assert valid == 1


class TestLogToolInvocationExtras:
    def test_explicit_source_overrides_inferred(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log_tool_invocation(
            session_key="dashboard:abc",
            source="cli",
            tool_name="t",
            outcome="approved",
        )
        assert log.recent()[0]["source"] == "cli"

    def test_request_id_coerced_to_string(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log_tool_invocation(
            session_key="cli_chat",
            tool_name="t",
            outcome="approved",
            request_id=42,
        )
        assert log.recent()[0]["request_id"] == "42"

    def test_metadata_is_persisted(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log_tool_invocation(
            session_key="cli_chat",
            tool_name="t",
            outcome="approved",
            metadata={"k": "v"},
        )
        assert log.recent()[0]["metadata"] == {"k": "v"}


class TestLogApiAccessExtras:
    def test_truncates_long_resources_and_error(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log_api_access(
            caller="alice",
            operation="op",
            outcome="failed",
            resources="r" * 800,
            error="e" * 800,
        )
        e = log.recent()[0]
        assert len(e["resources"]) == 500
        assert len(e["error"]) == 500


class TestRecentExtras:
    def test_respects_limit(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        for i in range(10):
            log.log(_make_event(event_id=f"e{i}"))
        events = log.recent(limit=3)
        assert len(events) == 3
        assert [e["event_id"] for e in events] == ["e9", "e8", "e7"]

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log(_make_event(event_id="good"))
        path = tmp_path / "security_events.jsonl"
        path.write_text(path.read_text() + "garbage-line\n")
        events = log.recent()
        assert len(events) == 1
        assert events[0]["event_id"] == "good"

    def test_recent_skips_blank_lines(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log(_make_event())
        path = tmp_path / "security_events.jsonl"
        path.write_text(path.read_text() + "\n   \n")
        assert len(log.recent()) == 1


class TestPruneExtras:
    def test_recomputes_last_hash_after_prune(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        log.log(_make_event(event_id="old", timestamp="2020-01-01T00:00:00+00:00"))
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc).isoformat()
        log.log(_make_event(event_id="fresh", timestamp=now))
        log.prune()
        log.log(_make_event(event_id="newer", timestamp=now))
        events = log.recent()
        assert events[0]["event_id"] == "newer"
        assert events[0]["prev_hash"] == events[1]["entry_hash"]

    def test_prune_removes_malformed_lines(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc).isoformat()
        log.log(_make_event(timestamp=now))
        path = tmp_path / "security_events.jsonl"
        path.write_text(path.read_text() + "not-json\n")
        assert log.prune() == 1

    def test_prune_keeps_when_nothing_old(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc).isoformat()
        log.log(_make_event(timestamp=now))
        assert log.prune() == 0
        assert len(log.recent()) == 1


class TestReadLastHashExtras:
    def test_scans_back_across_4kb_boundary(self, tmp_path: Path) -> None:
        log = SecurityEventLog(base_dir=tmp_path)
        big_resources = "x" * 200
        for i in range(60):
            log.log(_make_event(event_id=f"e{i:02d}", resources=big_resources))
        expected_tail = log._last_hash

        SecurityEventLog._instance = None
        SecurityEventLog._initialized = False
        log2 = SecurityEventLog(base_dir=tmp_path)
        assert log2._last_hash == expected_tail

    def test_corrupt_file_falls_back_to_empty(self, tmp_path: Path) -> None:
        SecurityEventLog._instance = None
        SecurityEventLog._initialized = False
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "security_events.jsonl").write_text("not json\n")
        log = SecurityEventLog(base_dir=tmp_path)
        assert log._last_hash == ""


class TestCallerScopeAttribution:
    """`G47` / AAPX-1: a SEL row carries the SUBSYSTEM (``caller_scope``) whose pass was
    running when it was written, read from ``guardrails.audit``'s one shared caller seam.

    This is what lets an audit distinguish a ladder step that RAN (and declined) from one
    that NEVER FIRED. Before AAPX-1 the two were the same observation from the log: two
    calls from the same session were byte-for-byte identical in every caller-attribution
    field, because the only such field was the session-key ``caller_identity``.
    """

    _VOLATILE = {"event_id", "timestamp", "prev_hash", "entry_hash"}

    def _attribution(self, row: dict) -> dict:
        return {k: v for k, v in row.items() if k not in self._VOLATILE}

    def _log_identical_call(self, log, *, ladder_ran: bool) -> None:
        """Log ONE call that is identical in every pre-AAPX-1 field; the only difference is
        whether a ladder subsystem scope was active (``ladder_ran``) or not."""
        import contextlib

        from gideon.security.guardrails.audit import caller_scope

        ctx = caller_scope("skill_ladder") if ladder_ran else contextlib.nullcontext()
        with ctx:
            log.log_tool_invocation(
                session_key="_bg",
                tool_name="one_shot_completion",
                tool_kind="model_call",
                outcome="completed",
            )

    def test_ran_and_declined_is_distinguishable_from_never_fired(self, tmp_path):
        log = SecurityEventLog(base_dir=tmp_path)
        self._log_identical_call(log, ladder_ran=True)
        self._log_identical_call(log, ladder_ran=False)
        rows = log.recent(limit=2)
        assert len(rows) == 2
        a, b = rows

        for shared in (
            "caller_identity",
            "source",
            "event_type",
            "operation",
            "tool_kind",
            "outcome",
        ):
            assert a[shared] == b[shared], shared
        assert a["caller_identity"] == "_bg"

        assert self._attribution(a) != self._attribution(b)

        assert sorted(r.get("caller_scope", "\x00missing") for r in rows) == [
            "",
            "skill_ladder",
        ]

    def test_explicit_caller_scope_on_event_is_not_overwritten(self, tmp_path):
        from gideon.security.guardrails.audit import caller_scope

        log = SecurityEventLog(base_dir=tmp_path)
        with caller_scope("skill_ladder"):
            log.log(_make_event(event_id="explicit", caller_scope="inbox_triage"))
        assert log.recent()[0]["caller_scope"] == "inbox_triage"

    def test_every_convenience_emitter_stamps_the_active_subsystem(self, tmp_path):
        """EVERY event carries the field, not just the tool-call one.

        ``log()`` is the single chokepoint, so the way this stays true is that no
        emitter builds its row around it. Walking the public ``log_*`` helpers is what
        would catch a new emitter that acquired its own write path.
        """
        from gideon.security.guardrails.audit import caller_scope

        log = SecurityEventLog(base_dir=tmp_path)
        helpers = sorted(
            name
            for name in dir(SecurityEventLog)
            if name.startswith("log_") and callable(getattr(SecurityEventLog, name))
        )
        assert helpers == ["log_api_access", "log_tool_invocation"], (
            "a SEL emitter was added or removed — extend this rail so the new one is "
            "held to the same attribution contract"
        )
        with caller_scope("inbox_triage"):
            log.log_tool_invocation(
                session_key="_bg", tool_name="read_file", outcome="completed"
            )
            log.log_api_access(
                caller="dashboard", operation="read_audit", outcome="allowed"
            )
        rows = log.recent(limit=2)
        assert len(rows) == 2
        assert sorted(r["event_type"] for r in rows) == [
            "api_access",
            "tool_invocation",
        ]
        for row in rows:
            assert row["caller_scope"] == "inbox_triage", row["event_type"]

    def test_a_caller_supplied_field_cannot_forge_the_subsystem(self, tmp_path):
        """The value is the emitting PASS's, never anything the caller handed in.

        No emitter takes ``caller_scope`` as an argument: the only way a value reaches
        the field is the ambient scope the running subsystem opened. Metadata is the
        one caller-controlled bag that reaches a row, so a ``caller_scope`` key in it
        must stay where it was put — nested, inert, and not the attribution the audit
        reads.
        """
        from gideon.security.guardrails.audit import caller_scope

        log = SecurityEventLog(base_dir=tmp_path)
        with caller_scope("skill_ladder"):
            log.log_tool_invocation(
                session_key="dashboard:abc",
                tool_name="execute_bash",
                outcome="completed",
                metadata={"caller_scope": "inbox_triage"},
            )
        row = log.recent()[0]
        assert row["caller_scope"] == "skill_ladder"
        assert row["metadata"]["caller_scope"] == "inbox_triage"
        assert row["caller_identity"] == "dashboard:abc"
        assert row["caller_scope"] != row["caller_identity"]
        assert row["caller_scope"] != row["agent"]
        assert log.verify_integrity() == (1, 1)

    def test_caller_scope_is_covered_by_the_hmac(self, tmp_path):
        from gideon.security.guardrails.audit import caller_scope

        log = SecurityEventLog(base_dir=tmp_path)
        with caller_scope("skill_ladder"):
            log.log(_make_event(event_id="chk"))
        assert log.verify_integrity() == (1, 1)
        path = tmp_path / "security_events.jsonl"
        row = json.loads(path.read_text().strip())
        assert row["caller_scope"] == "skill_ladder"
        row["caller_scope"] = "inbox_triage"
        path.write_text(json.dumps(row) + "\n")
        total, valid = log.verify_integrity()
        assert (total, valid) == (1, 0)


class TestRotate:
    """rotate() ARCHIVES the log and starts a fresh chain — it does NOT rotate the HMAC key.

    This pins the operational truth behind the dashboard relabel (issue 534): the "Rotate"
    control archives the live log to a timestamped ``.bak.jsonl`` and resets the chain, while
    the create-only signing key is left byte-for-byte untouched.
    """

    def test_archive_preserves_key_and_starts_clean_chain(self, log, sel_dir):
        for i in range(3):
            log.log(_make_event(event_id=f"pre{i}"))
        key_path = sel_dir / "sel_hmac.key"
        key_before = key_path.read_bytes()

        result = log.rotate(archive=True)

        assert key_path.read_bytes() == key_before

        assert result["rotated"] is True
        assert result["entries_before"] == 3
        assert result["entries_after"] == 0
        archive = Path(result["archive_path"])
        assert archive.exists()
        assert archive.name.startswith("security_events.")
        assert archive.name.endswith(".bak.jsonl")
        assert len([ln for ln in archive.read_text().splitlines() if ln.strip()]) == 3

        assert not (sel_dir / "security_events.jsonl").exists()
        assert log.verify_integrity() == (0, 0)
        log.log(_make_event(event_id="post"))
        total, valid = log.verify_integrity()
        assert (total, valid) == (1, 1)
        assert log.recent()[0]["prev_hash"] == ""

    def test_no_archive_still_leaves_key_untouched(self, log, sel_dir):
        log.log(_make_event(event_id="pre"))
        key_before = (sel_dir / "sel_hmac.key").read_bytes()

        result = log.rotate(archive=False)

        assert result["rotated"] is True
        assert result["archive_path"] == ""
        assert not (sel_dir / "security_events.jsonl").exists()
        assert (sel_dir / "sel_hmac.key").read_bytes() == key_before
        assert log.verify_integrity() == (0, 0)


class TestOutcomeReason:
    """``error`` belongs to denied/failed outcomes; a success carries its rationale
    as ``metadata['reason']`` (BACKLOG-13)."""

    def test_granted_event_moves_rationale_out_of_error(self, log):
        log.log_api_access(
            caller="127.0.0.1",
            operation="internal_auth",
            outcome="granted",
            source="token_auth",
            resources="/api/spawn",
            error="cookie auth (no secret header)",
        )
        event = log.recent()[0]
        assert event["error"] == ""
        assert event["metadata"]["reason"] == "cookie auth (no secret header)"

    def test_granted_event_keeps_explicit_reason_metadata(self, log):
        log.log_api_access(
            caller="127.0.0.1",
            operation="internal_auth",
            outcome="granted",
            source="token_auth",
            metadata={"reason": "cookie auth (no secret header)"},
        )
        event = log.recent()[0]
        assert event["error"] == ""
        assert event["metadata"] == {"reason": "cookie auth (no secret header)"}

    def test_explicit_reason_wins_over_a_stray_error(self, log):
        log.log_api_access(
            caller="127.0.0.1",
            operation="internal_auth",
            outcome="granted",
            error="stray",
            metadata={"reason": "cookie auth"},
        )
        event = log.recent()[0]
        assert event["error"] == ""
        assert event["metadata"]["reason"] == "cookie auth"

    def test_denied_event_keeps_its_error(self, log):
        log.log_api_access(
            caller="127.0.0.1",
            operation="internal_auth",
            outcome="denied",
            source="token_auth",
            error="wrong secret",
        )
        event = log.recent()[0]
        assert event["error"] == "wrong secret"
        assert "reason" not in event["metadata"]

    @pytest.mark.parametrize("outcome", ["failure", "failed", "error", "not_found"])
    def test_failed_outcomes_keep_their_error(self, log, outcome):
        log.log_tool_invocation(
            session_key="cli_chat",
            tool_name="t",
            outcome=outcome,
            error="boom",
        )
        event = log.recent()[0]
        assert event["error"] == "boom"

    def test_success_tool_invocation_rationale_becomes_a_reason(self, log):
        log.log_tool_invocation(
            session_key="cli_chat",
            tool_name="t",
            outcome="approved",
            error="auto-approved by policy",
            metadata={"k": "v"},
        )
        event = log.recent()[0]
        assert event["error"] == ""
        assert event["metadata"] == {"k": "v", "reason": "auto-approved by policy"}

    def test_normalized_record_still_verifies(self, log):
        log.log_api_access(
            caller="127.0.0.1",
            operation="internal_auth",
            outcome="granted",
            error="cookie auth (no secret header)",
        )
        assert log.verify_integrity() == (1, 1)

    def test_no_success_emitter_passes_a_literal_error(self):
        """The rail: no call site hands a success outcome a literal ``error=``."""
        import ast

        from gideon.security.sel import AUDIT_OUTCOME_SUCCESS

        root = Path(__file__).resolve().parents[2] / "runtime" / "gideon"
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )
                if name not in (
                    "log_api_access",
                    "log_tool_invocation",
                    "SecurityEvent",
                ):
                    continue
                kwargs = {k.arg: k.value for k in node.keywords if k.arg}
                outcome, error = kwargs.get("outcome"), kwargs.get("error")
                if not isinstance(outcome, ast.Constant) or not isinstance(
                    error, ast.Constant
                ):
                    continue
                if outcome.value in AUDIT_OUTCOME_SUCCESS and error.value:
                    offenders.append(f"{path}:{node.lineno} outcome={outcome.value!r}")
        assert not offenders, (
            "a successful outcome must carry its rationale as metadata['reason'], "
            "never as error=:\n" + "\n".join(offenders)
        )

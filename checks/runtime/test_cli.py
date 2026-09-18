"""Tests for CLI module."""

import argparse
import json
import subprocess
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from gideon.interfaces.cli.commands import _cron
from gideon.interfaces.cli.doctor import _doctor


class TestDoctor:
    def test_doctor_with_agent(self, tmp_path):
        agent_file = tmp_path / "gideon.json"
        agent_file.write_text("{}")
        mock_run = MagicMock(returncode=0, stdout="gideon-cli 1.0.0", stderr="")
        with (
            patch(
                "gideon.interfaces.cli.doctor.shutil.which",
                side_effect=lambda b: f"/usr/local/bin/{b}",
            ),
            patch("gideon.interfaces.cli.doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("no gateway"),
            ),
            patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
        ):
            _doctor()

    def test_doctor_without_agent(self):
        with (
            patch("gideon.interfaces.cli.doctor.shutil.which", return_value=None),
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("no gateway"),
            ),
            patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
        ):
            try:
                _doctor()
            except SystemExit as e:
                assert e.code == 1


class _TtyStdin:
    """A stdin that claims to be a terminal.

    `cli_setup._ask` takes the non-interactive door when `sys.stdin.isatty()` is False
    (PUBL-7: `setup` must not die with an EOFError traceback when it cannot prompt).
    pytest's stdin is not a tty, so a test that patches `builtins.input` to drive a prompt
    must also say it is on a terminal — otherwise the guard returns "" and the patched
    answer is never consumed, which reads as the feature being broken.
    """

    def isatty(self) -> bool:
        return True


class TestSetupWorkspaceDir:
    """Tests for _setup_workspace_dir prompt default and label logic."""

    def test_uses_saved_path_as_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gideon.interfaces.cli.setup.sys.stdin", _TtyStdin())
        ws_file = tmp_path / "workspace_dir"
        ws_file.write_text("/custom/workspace\n")
        custom_dir = tmp_path / "custom"
        monkeypatch.setattr(
            "gideon.interfaces.cli.setup._workspace_dir_file", lambda: ws_file
        )
        with patch("builtins.input", return_value=str(custom_dir)) as mock_input:
            from gideon.interfaces.cli.setup import _setup_workspace_dir

            _setup_workspace_dir()
        prompt = mock_input.call_args[0][0]
        assert "/custom/workspace" in prompt

    def test_shows_configured_label_when_saved(self, tmp_path, monkeypatch, capsys):
        ws_file = tmp_path / "workspace_dir"
        ws_file.write_text("/custom/workspace\n")
        custom_dir = tmp_path / "custom"
        monkeypatch.setattr(
            "gideon.interfaces.cli.setup._workspace_dir_file", lambda: ws_file
        )
        with patch("builtins.input", return_value=str(custom_dir)):
            from gideon.interfaces.cli.setup import _setup_workspace_dir

            _setup_workspace_dir()
        output = capsys.readouterr().out
        assert "Configured:" in output

    def test_shows_default_label_when_no_saved(self, tmp_path, monkeypatch, capsys):
        ws_file = tmp_path / "no_such_file"
        custom_dir = tmp_path / "ws"
        monkeypatch.setattr(
            "gideon.interfaces.cli.setup._workspace_dir_file", lambda: ws_file
        )
        with patch("builtins.input", return_value=str(custom_dir)):
            from gideon.interfaces.cli.setup import _setup_workspace_dir

            _setup_workspace_dir()
        output = capsys.readouterr().out
        assert "Default:" in output


class TestCronCli:
    """The `cron` CLI writes the unified TRIGGER STORE (S108).

    🔴 Every test here used to `patch("gideon.interfaces.cli.commands.ScheduleService")` and assert the
    `add_job(...)` CALL SHAPE. They passed the whole time a CLI-created cron DID NOT FIRE: the write
    went to `crons.json`, which the clock engine never reads, so the job stayed inert until the user
    restarted the gateway. A mock-shape assertion cannot see that — it proves only which
    function was called, not that anything got scheduled. These drive the store and assert the row.

    They also had no `config_dir` isolation at all (the mock was the only thing between them and
    the user's real home). The store is a real file, so the fixture now redirects it.
    """

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "gideon.interfaces.cli.commands.config_dir", lambda: tmp_path
        )
        monkeypatch.setattr("gideon.interfaces.cli.commands.sel", MagicMock())
        return tmp_path

    def _store(self, tmp_path):
        from gideon.automation.triggers.store import TriggerStore

        return TriggerStore(base_dir=tmp_path)

    def _only(self, tmp_path):
        rows = self._store(tmp_path).load()
        assert len(rows) == 1, [r.trigger.id for r in rows]
        return rows[0]

    def test_cron_add_writes_an_armed_store_trigger(self, tmp_path):
        """🔴 THE POINT OF THE SESSION: a created cron must be able to fire without a restart.
        `service.due_ids` only surfaces rows carrying a `next_fire_at`."""
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel=None,
                approval_mode="",
            )
        )
        row = self._only(tmp_path)
        assert row.ok, row.errors
        assert row.trigger.enabled
        assert row.trigger.next_fire_at
        assert row.trigger.spec == {"kind": "interval", "interval_secs": 300}
        assert not (
            tmp_path / "crons.json"
        ).exists(), "nothing may be written to the legacy file"

    def test_cron_add_with_channel(self, tmp_path):
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel="C0AP77JJSN6",
                approval_mode="",
            )
        )
        assert self._only(tmp_path).trigger.delivery == "channel:C0AP77JJSN6"

    def test_cron_add_with_cron_expr(self, tmp_path):
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=None,
                cron_expr="0 9 * * MON-FRI",
                channel="C0AP77JJSN6",
                approval_mode="",
            )
        )
        row = self._only(tmp_path)
        assert row.trigger.spec == {"kind": "cron", "expr": "0 9 * * MON-FRI"}
        assert row.trigger.delivery == "channel:C0AP77JJSN6"
        assert row.trigger.next_fire_at

    def test_cron_add_with_approval_mode(self, tmp_path):
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel=None,
                approval_mode="auto",
            )
        )
        inline = (self._only(tmp_path).trigger.workflow or {}).get("inline") or {}
        config = inline.get("config") or {}
        assert config.get("approval_mode") == "auto"
        assert config.get("task_template") == "check"

    def test_cron_add_is_a_user_creation_not_an_agent_one(self, tmp_path):
        """`created_by="user"`: the agent cap (decision 5d) bounds what the ASSISTANT creates
        unprompted. A human typing the command is the user acting directly, and capping their own
        CLI at the agent limit would aim the rule at the wrong party."""
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel=None,
                approval_mode="",
            )
        )
        assert self._only(tmp_path).trigger.created_by == "user"

    def test_cron_add_without_a_cadence_is_refused(self, tmp_path, capsys):
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=None,
                cron_expr=None,
                channel=None,
                approval_mode="",
            )
        )
        assert "Provide --every or --cron" in capsys.readouterr().out
        assert self._store(tmp_path).load() == []

    def _seed(self, tmp_path, **over):
        from gideon.automation.triggers.models import Trigger

        store = self._store(tmp_path)
        trigger = Trigger(
            id="clock:ops",
            name="ops",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 300},
            workflow={
                "inline": {
                    "provider": "invoke-agent",
                    "config": {
                        "task_template": "check",
                        "agent": "helper",
                        "model": "gpt",
                    },
                }
            },
        )
        for key, value in over.items():
            setattr(trigger, key, value)
        store.upsert(trigger)
        return store

    def test_cron_update_approval_mode(self, tmp_path):
        self._seed(tmp_path)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel=None,
                approval_mode="auto",
            )
        )
        config = ((self._only(tmp_path).trigger.workflow or {})["inline"]).get(
            "config"
        ) or {}
        assert config.get("approval_mode") == "auto"
        assert config.get("agent") == "helper"
        assert config.get("model") == "gpt"

    def test_cron_update_default_approval_mode_clears_it(self, tmp_path):
        self._seed(tmp_path)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel=None,
                approval_mode="default",
            )
        )
        config = ((self._only(tmp_path).trigger.workflow or {})["inline"]).get(
            "config"
        ) or {}
        assert config.get("approval_mode") == ""

    def test_cron_update_cadence_re_arms(self, tmp_path, monkeypatch):
        """🔴 Found by driving: the cadence changed and the list showed the new time, but
        `next_fire_at` still held the OLD one — so the job would fire on the schedule the user had
        just replaced. `next_fire_at` is engine state the patch allowlist refuses, so the re-arm
        is a separate clear-then-arm."""
        monkeypatch.setenv("TZ", "UTC")
        store = self._seed(tmp_path, next_fire_at="2026-01-01T09:00:00+00:00")
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr="30 7 * * *",
                channel=None,
                approval_mode=None,
            )
        )
        trigger = store.get("clock:ops").trigger
        assert trigger.spec["expr"] == "30 7 * * *"
        assert trigger.next_fire_at.endswith("07:30:00+00:00")

    def test_cron_update_cadence_preserves_the_quietly_losable_keys(self, tmp_path):
        """`timezone`/`skip_dates`/`strict` survive a cadence change — §1.3's contract. A user
        changing `0 9 * * *` to `0 10 * * *` must not lose their holidays."""
        store = self._seed(tmp_path)
        trigger = store.get("clock:ops").trigger
        trigger.spec = {
            **trigger.spec,
            "timezone": "America/New_York",
            "skip_dates": ["2026-12-25"],
        }
        store.upsert(trigger)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr="0 10 * * *",
                channel=None,
                approval_mode=None,
            )
        )
        spec = store.get("clock:ops").trigger.spec
        assert spec["expr"] == "0 10 * * *"
        assert spec["timezone"] == "America/New_York"
        assert spec["skip_dates"] == ["2026-12-25"]

    def test_cron_update_whitespace_channel_skipped(self, tmp_path, capsys):
        self._seed(tmp_path)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel="   ",
                approval_mode=None,
            )
        )
        assert "Provide at least one field to update" in capsys.readouterr().out

    def test_cron_update_every_and_cron_exclusive(self, tmp_path, capsys):
        self._seed(tmp_path)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=600,
                cron_expr="0 9 * * *",
                channel=None,
                approval_mode=None,
            )
        )
        assert "Provide --every or --cron, not both" in capsys.readouterr().out
        assert self._only(tmp_path).trigger.spec == {
            "kind": "interval",
            "interval_secs": 300,
        }

    def test_cron_update_not_found(self, tmp_path, capsys):
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="nope",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel="C0AP77JJSN6",
                approval_mode=None,
            )
        )
        assert "Job not found: nope" in capsys.readouterr().out

    def test_cron_pause_and_resume(self, tmp_path):
        store = self._seed(tmp_path, enabled=True)
        _cron(argparse.Namespace(cron_action="pause", job_id="clock:ops"))
        assert store.get("clock:ops").trigger.enabled is False
        _cron(argparse.Namespace(cron_action="resume", job_id="clock:ops"))
        assert store.get("clock:ops").trigger.enabled is True

    def test_resuming_a_broken_trigger_names_the_parse_error(self, tmp_path, capsys):
        """`set_paused` REFUSES to enable a row that failed to parse and says why, which beats the
        legacy "Job not found" — the row does exist, so that message was wrong as well as unhelpful.
        """
        self._seed(tmp_path, spec={}, enabled=False)
        _cron(argparse.Namespace(cron_action="resume", job_id="clock:ops"))
        out = capsys.readouterr().out
        assert "parse error" in out
        assert self._store(tmp_path).get("clock:ops").trigger.enabled is False

    def test_cron_remove_deletes_the_row(self, tmp_path):
        store = self._seed(tmp_path)
        _cron(argparse.Namespace(cron_action="remove", job_id="clock:ops"))
        assert store.get("clock:ops") is None

    def test_cron_remove_not_found(self, tmp_path, capsys):
        _cron(argparse.Namespace(cron_action="remove", job_id="nope"))
        assert "Job not found: nope" in capsys.readouterr().out

    def test_cron_list_renders_the_stores_rows(self, tmp_path, capsys):
        self._seed(tmp_path)
        _cron(argparse.Namespace(cron_action="list"))
        out = capsys.readouterr().out
        assert "clock:ops" in out
        assert "check" in out

    def test_cron_list_shows_a_broken_row_rather_than_hiding_it(self, tmp_path, capsys):
        """The legacy list could not represent a broken row at all, and silently omitting a trigger
        the user created is how "where did my automation go" happens."""
        self._seed(tmp_path, spec={})
        _cron(argparse.Namespace(cron_action="list"))
        out = capsys.readouterr().out
        assert "clock:ops" in out
        assert "⚠️" in out

    def test_cron_list_when_empty(self, tmp_path, capsys):
        _cron(argparse.Namespace(cron_action="list"))
        assert "No cron jobs." in capsys.readouterr().out

    def test_the_cli_no_longer_touches_the_legacy_service(self):
        """🔴 The clean break, pinned at the source: a re-added `ScheduleService` write here would
        silently stop firing again, and the symptom (a cron that runs only after a restart) is
        exactly the one that took this long to notice."""
        import inspect

        from gideon.interfaces.cli import commands as cli_commands

        assert "ScheduleService" not in inspect.getsource(cli_commands._cron)


class TestSetupTimezone:
    def test_auto_detect_from_tz_env(self, monkeypatch):
        """TZ env var is checked before /etc/localtime."""
        from gideon.interfaces.cli.setup import _detect_system_timezone

        monkeypatch.setenv("TZ", "Europe/London")
        assert _detect_system_timezone() == "Europe/London"

    def test_auto_detect_tz_env_with_colon(self, monkeypatch):
        """TZ env var with glibc colon prefix is handled."""
        from gideon.interfaces.cli.setup import _detect_system_timezone

        monkeypatch.setenv("TZ", ":America/Chicago")
        assert _detect_system_timezone() == "America/Chicago"

    def test_auto_detect_from_symlink(self, tmp_path, monkeypatch):
        """When /etc/localtime is a symlink, timezone is auto-detected."""
        monkeypatch.setattr("gideon.interfaces.cli.setup.sys.stdin", _TtyStdin())
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("gideon.interfaces.cli.setup.config_path", lambda: cfg_file)

        from gideon.interfaces.cli.setup import _setup_timezone

        with patch("builtins.input", return_value="") as mock_input:
            with patch(
                "gideon.interfaces.cli.setup._detect_system_timezone",
                return_value="America/Los_Angeles",
            ):
                _setup_timezone()

        prompt = mock_input.call_args[0][0]
        assert "America/Los_Angeles" in prompt
        data = json.loads(cfg_file.read_text())
        assert data["timezone"] == "America/Los_Angeles"

    def test_manual_entry(self, tmp_path, monkeypatch):
        """When no auto-detect, user types timezone manually."""
        monkeypatch.setattr("gideon.interfaces.cli.setup.sys.stdin", _TtyStdin())
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("gideon.interfaces.cli.setup.config_path", lambda: cfg_file)

        from gideon.interfaces.cli.setup import _setup_timezone

        with patch("builtins.input", return_value="America/New_York"):
            with patch(
                "gideon.interfaces.cli.setup._detect_system_timezone", return_value=""
            ):
                _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert data["timezone"] == "America/New_York"

    def test_skip_on_empty_input(self, tmp_path, monkeypatch):
        """Empty input skips timezone setup."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("gideon.interfaces.cli.setup.config_path", lambda: cfg_file)

        from gideon.interfaces.cli.setup import _setup_timezone

        with patch("builtins.input", return_value=""):
            with patch(
                "gideon.interfaces.cli.setup._detect_system_timezone", return_value=""
            ):
                _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert "timezone" not in data

    def test_invalid_timezone_rejected(self, tmp_path, monkeypatch, capsys):
        """Invalid timezone is rejected, not saved."""
        monkeypatch.setattr("gideon.interfaces.cli.setup.sys.stdin", _TtyStdin())
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("gideon.interfaces.cli.setup.config_path", lambda: cfg_file)

        from gideon.interfaces.cli.setup import _setup_timezone

        with patch("builtins.input", return_value="Invalid/Timezone"):
            with patch(
                "gideon.interfaces.cli.setup._detect_system_timezone", return_value=""
            ):
                _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert "timezone" not in data
        output = capsys.readouterr().out
        assert "Unknown timezone" in output

    def test_keeps_existing_on_enter(self, tmp_path, monkeypatch):
        """Re-running setup with existing timezone keeps it on Enter."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"timezone": "America/Chicago"}))
        monkeypatch.setattr("gideon.interfaces.cli.setup.config_path", lambda: cfg_file)

        from gideon.interfaces.cli.setup import _setup_timezone

        with patch("builtins.input", return_value=""):
            _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert data["timezone"] == "America/Chicago"

    def test_corrupted_config_not_overwritten(self, tmp_path, monkeypatch, capsys):
        """Corrupted config file is not overwritten."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("not json {{{")
        monkeypatch.setattr("gideon.interfaces.cli.setup.config_path", lambda: cfg_file)

        from gideon.interfaces.cli.setup import _setup_timezone

        _setup_timezone()

        assert cfg_file.read_text() == "not json {{{"
        output = capsys.readouterr().out
        assert "Could not read" in output


class TestLogout:
    """Tests for _logout CLI function."""

    def test_logout_success(self, tmp_path, monkeypatch):
        """Successful logout prints success message."""
        secret_file = tmp_path / ".local_secret"
        secret_file.write_text("test-secret")
        monkeypatch.setattr("gideon.interfaces.cli.server.config_dir", lambda: tmp_path)

        from gideon.interfaces.cli.server import _logout

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _logout(7777)

    def test_logout_gateway_not_running(self, tmp_path, monkeypatch):
        """Missing secret file means gateway not running."""
        monkeypatch.setattr("gideon.interfaces.cli.server.config_dir", lambda: tmp_path)

        from gideon.interfaces.cli.server import _logout

        try:
            _logout(7777)
            assert False, "should have exited"
        except SystemExit as e:
            assert e.code == 1

    def test_logout_http_error(self, tmp_path, monkeypatch):
        """HTTP error from gateway is handled."""
        secret_file = tmp_path / ".local_secret"
        secret_file.write_text("test-secret")
        monkeypatch.setattr("gideon.interfaces.cli.server.config_dir", lambda: tmp_path)

        from gideon.interfaces.cli.server import _logout

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(None, 403, "Forbidden", {}, None),
        ):
            try:
                _logout(7777)
                assert False, "should have exited"
            except SystemExit as e:
                assert e.code == 1

    def test_logout_connection_error(self, tmp_path, monkeypatch):
        """Connection error means gateway not running."""
        secret_file = tmp_path / ".gideon" / ".local_secret"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text("test-secret")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        from gideon.interfaces.cli.server import _logout

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            try:
                _logout(7777)
                assert False, "should have exited"
            except SystemExit as e:
                assert e.code == 1

    def test_logout_error_response(self, tmp_path, monkeypatch):
        """Error response from gateway is handled."""
        secret_file = tmp_path / ".gideon" / ".local_secret"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text("test-secret")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        from gideon.interfaces.cli.server import _logout

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": false, "error": "test error"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            try:
                _logout(7777)
                assert False, "should have exited"
            except SystemExit as e:
                assert e.code == 1


class TestStatus:
    """Tests for _status() HTTP error handling."""

    def _make_args(self, port=7777):
        return argparse.Namespace(port=port)

    def test_status_auth_required(self, capsys):
        """401/403 should report gateway as running with token auth."""
        from gideon.interfaces.cli.server import _status

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://127.0.0.1:7777/api/status", 403, "Forbidden", {}, None
            ),
        ):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "token auth" in out

    def test_status_other_http_error(self, capsys):
        """Non-auth HTTP errors should report gateway as running with code."""
        from gideon.interfaces.cli.server import _status

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://127.0.0.1:7777/api/status",
                500,
                "Internal Server Error",
                {},
                None,
            ),
        ):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "HTTP 500" in out

    def test_status_connection_refused(self, capsys):
        """Connection refused should report gateway as not running."""
        from gideon.interfaces.cli.server import _status

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "not running" in out

    def test_status_success(self, capsys):
        """200 OK should display the counters the real payload carries.

        The keys are the ones ``handlers_system.api_status`` ships; the end-to-end proof
        that this IS the real shape lives in ``test_cli_status_counters``.
        """
        from gideon.interfaces.cli.server import _status

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "uptime": "1h 0m",
                "sessions": 2,
                "subagents": 0,
                "cron": {"total": 1, "enabled": 1, "broken": 0},
                "stats": {"total_turns": 12},
                "lessons": 3,
            }
        ).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "1h 0m" in out
        assert "Sessions:    2" in out
        assert "Schedules:   1 (1 enabled)" in out
        assert "Turns:       12" in out
        assert "Lessons:     3" in out
        assert "unknown" not in out

    def test_status_unexpected_exception(self, capsys):
        """Non-network exceptions should report gateway as running with unexpected response."""
        from gideon.interfaces.cli.server import _status

        with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "unexpected response" in out


class TestIsGideonProcess:
    """Tests for _is_gideon_process helper."""

    def test_returns_true_for_gideon(self):
        from gideon.interfaces.cli.server import _is_gideon_process

        with patch(
            "subprocess.check_output",
            return_value="python3 -m gideon.interfaces.dashboard\n",
        ):
            assert _is_gideon_process(1234) is True

    def test_returns_true_for_gideon_binary(self):
        from gideon.interfaces.cli.server import _is_gideon_process

        with patch("subprocess.check_output", return_value="/usr/bin/gideon start\n"):
            assert _is_gideon_process(1234) is True

    def test_returns_false_for_unrelated(self):
        from gideon.interfaces.cli.server import _is_gideon_process

        with patch("subprocess.check_output", return_value="nginx: worker process\n"):
            assert _is_gideon_process(1234) is False

    def test_returns_false_for_broad_match(self):
        """Editing a gideon file should NOT match — only gateway entry points."""
        from gideon.interfaces.cli.server import _is_gideon_process

        with patch(
            "subprocess.check_output", return_value="vim /tmp/gideon-notes.txt\n"
        ):
            assert _is_gideon_process(1234) is False

    def test_returns_false_on_process_exit(self):
        from gideon.interfaces.cli.server import _is_gideon_process

        with patch(
            "subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "ps"),
        ):
            assert _is_gideon_process(1234) is False

    def test_raises_on_missing_ps(self):
        from gideon.interfaces.cli.server import _is_gideon_process

        with patch("subprocess.check_output", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                _is_gideon_process(1234)


class TestStop:
    """Tests for _stop CLI function."""

    def _mock_sel(self):
        mock = MagicMock()
        return patch("gideon.interfaces.cli.commands.sel", return_value=mock)

    @pytest.fixture(autouse=True)
    def _no_service(self):
        with patch(
            "gideon.interfaces.cli.server.service_controller.stop_service",
            return_value=False,
        ):
            yield

    def test_lsof_not_found(self, capsys):
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch("subprocess.check_output", side_effect=FileNotFoundError),
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "lsof" in capsys.readouterr().out

    def test_no_process_on_port(self, capsys):
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=subprocess.CalledProcessError(1, "lsof"),
            ),
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "No Gideon gateway" in capsys.readouterr().out

    def test_no_gideon_process(self, capsys):
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=[
                    "1234\n",
                    "nginx: worker\n",
                ],
            ),
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "No Gideon gateway" in capsys.readouterr().out

    def test_ps_not_found(self, capsys):
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=[
                    "1234\n",
                    FileNotFoundError,
                ],
            ),
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "ps" in capsys.readouterr().out

    def test_successful_stop(self, capsys):
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=[
                    "1234\n",
                    "python3 -m gideon.interfaces.dashboard\n",
                ],
            ),
            patch("os.kill"),
            patch("time.sleep"),
        ):
            _stop(7777)
        assert "SIGTERM" in capsys.readouterr().out

    def test_permission_denied(self, capsys):
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=[
                    "1234\n",
                    "python3 -m gideon.interfaces.dashboard\n",
                ],
            ),
            patch("os.kill", side_effect=PermissionError),
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "No permission" in capsys.readouterr().out

    def test_process_already_exited(self, capsys):
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=[
                    "1234\n",
                    "python3 -m gideon.interfaces.dashboard\n",
                ],
            ),
            patch("os.kill", side_effect=ProcessLookupError),
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "already exited" in capsys.readouterr().out

    def test_partial_permission_denied(self, capsys):
        """One PID succeeds, another is denied — reports both."""
        from gideon.interfaces.cli.server import _stop

        def kill_side_effect(pid, sig):
            if pid == 5678:
                raise PermissionError

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=[
                    "1234\n5678\n",
                    "python3 -m gideon.interfaces.dashboard\n",
                    "python3 -m gideon.interfaces.dashboard\n",
                ],
            ),
            patch("os.kill", side_effect=kill_side_effect),
            patch("time.sleep"),
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "SIGTERM" in out
        assert "No permission" in out

    def test_lsof_with_warnings(self, capsys):
        """lsof sometimes emits warnings mixed with PIDs — non-digit lines are filtered."""
        from gideon.interfaces.cli.server import _stop

        with (
            self._mock_sel(),
            patch(
                "subprocess.check_output",
                side_effect=[
                    "1234\nlsof: WARNING: can't stat() ...\n",
                    "python3 -m gideon.interfaces.dashboard\n",
                ],
            ),
            patch("os.kill"),
            patch("time.sleep"),
        ):
            _stop(7777)
        assert "SIGTERM" in capsys.readouterr().out


class TestResolveClientPort:
    """Tests for `resolve_client_port` — the port-resolution order used by
    `gideon token` / `status` / `logout` / `stop` to find the gateway.

    Resolution order (see cli.resolve_client_port):
      1. explicit --port CLI arg (cli_port != None)
      2. GIDEON_PORT env var
      3. port parsed from dashboard.url in config
      4. default 10000
    """

    def test_cli_flag_wins(self, monkeypatch, tmp_path):
        """An explicit --port flag must override env and config."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.setenv("GIDEON_PORT", "9999")
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:8888"
        with patch(
            "gideon.interfaces.cli.server.AppConfig.load", return_value=mock_cfg
        ):
            assert resolve_client_port(12345) == 12345

    def test_env_var_used_when_no_cli(self, monkeypatch):
        """GIDEON_PORT env var wins over config when no --port passed."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.setenv("GIDEON_PORT", "6777")
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:8888"
        with patch(
            "gideon.interfaces.cli.server.AppConfig.load", return_value=mock_cfg
        ):
            assert resolve_client_port(None) == 6777

    def test_invalid_env_var_falls_through_to_config(self, monkeypatch):
        """A garbage GIDEON_PORT must not crash; the helper falls through."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.setenv("GIDEON_PORT", "not-a-number")
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:7778"
        with patch(
            "gideon.interfaces.cli.server.AppConfig.load", return_value=mock_cfg
        ):
            assert resolve_client_port(None) == 7778

    def test_config_url_used_when_no_cli_no_env(self, monkeypatch):
        """The port in dashboard.url must be honoured when env is unset."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.delenv("GIDEON_PORT", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:7778"
        with patch(
            "gideon.interfaces.cli.server.AppConfig.load", return_value=mock_cfg
        ):
            assert resolve_client_port(None) == 7778

    def test_config_url_hostname_only_falls_through_to_default(self, monkeypatch):
        """A dashboard.url without an explicit port must fall through to 10000."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.delenv("GIDEON_PORT", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://my.host.example"
        with patch(
            "gideon.interfaces.cli.server.AppConfig.load", return_value=mock_cfg
        ):
            assert resolve_client_port(None) == 10000

    def test_empty_config_falls_through_to_default(self, monkeypatch):
        """No env, empty dashboard.url → 10000."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.delenv("GIDEON_PORT", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = ""
        with patch(
            "gideon.interfaces.cli.server.AppConfig.load", return_value=mock_cfg
        ):
            assert resolve_client_port(None) == 10000

    def test_config_load_failure_falls_through_to_default(self, monkeypatch):
        """If config loading raises, the helper must still return a usable port."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.delenv("GIDEON_PORT", raising=False)
        with patch(
            "gideon.interfaces.cli.server.AppConfig.load",
            side_effect=RuntimeError("boom"),
        ):
            assert resolve_client_port(None) == 10000

    def test_cli_flag_zero_is_respected(self, monkeypatch):
        """Port 0 is weird but valid; it must not be coerced to None/default."""
        from gideon.interfaces.cli.server import resolve_client_port

        monkeypatch.setenv("GIDEON_PORT", "9999")
        assert resolve_client_port(0) == 0


class TestDoctorStaleProjectDir:
    """Tests for doctor stale project_dir detection."""

    def test_doctor_detects_stale_project_dir(self, tmp_path, capsys):
        proj_file = tmp_path / "project_dir"
        proj_file.write_text("/nonexistent/deleted\n")
        agent_file = tmp_path / "gideon.json"
        agent_data = {
            "tools": ["@gideon-core", "@gideon-schedule"],
            "allowedTools": ["@gideon-core", "@gideon-schedule"],
            "mcpServers": {
                "gideon-core": {
                    "command": "/usr/local/bin/gideon",
                    "args": ["mcp-core"],
                },
                "gideon-schedule": {
                    "command": "/usr/local/bin/gideon",
                    "args": ["mcp-schedule"],
                },
            },
        }
        agent_file.write_text(json.dumps(agent_data))
        mock_run = MagicMock(returncode=0, stdout="gideon-cli 1.0.0", stderr="")
        with (
            patch(
                "gideon.interfaces.cli.doctor.shutil.which",
                side_effect=lambda b: f"/usr/local/bin/{b}",
            ),
            patch("gideon.interfaces.cli.doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen"),
            patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
            patch("gideon.interfaces.cli.doctor.config_dir", return_value=tmp_path),
            patch.dict("os.environ", {"GIDEON_PROJECT_DIR": ""}, clear=False),
        ):
            with pytest.raises(SystemExit):
                _doctor()
        out = capsys.readouterr().out
        assert "stale" in out
        assert "project dir: ⚠️  not set" not in out


class TestDoctorMcpCmdFixed:
    """Tests for doctor auto-fixing stale MCP binary paths."""

    def test_doctor_fixes_stale_mcp_path(self, tmp_path, capsys):
        agent_file = tmp_path / "gideon.json"
        valid_bin = tmp_path / "gideon"
        valid_bin.write_text("#!/bin/sh")
        valid_bin.chmod(0o755)
        agent_data = {
            "tools": ["@gideon-core", "@gideon-schedule"],
            "allowedTools": ["@gideon-core", "@gideon-schedule"],
            "mcpServers": {
                "gideon-core": {"command": "/nonexistent/gideon", "args": ["mcp-core"]},
                "gideon-schedule": {
                    "command": str(valid_bin),
                    "args": ["mcp-schedule"],
                },
            },
        }
        agent_file.write_text(json.dumps(agent_data))
        mock_run = MagicMock(returncode=0, stdout="gideon-cli 1.0.0", stderr="")

        def which_side_effect(b):
            if b == "gideon":
                return "/usr/bin/gideon"
            return f"/usr/local/bin/{b}"

        with (
            patch(
                "gideon.interfaces.cli.doctor.shutil.which",
                side_effect=which_side_effect,
            ),
            patch("gideon.interfaces.cli.doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen"),
            patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.dict("os.environ", {"GIDEON_PROJECT_DIR": ""}, clear=False),
            patch(
                "gideon.extensions.providers.use_cases.load_use_case_settings",
                return_value={"enabled": False},
            ),
            patch("gideon.integrations.stt.registry.active_stt", return_value=None),
        ):
            _doctor()
        out = capsys.readouterr().out
        assert "fixed stale path" in out
        assert "Auto-fixed 1 stale binary path in gideon.json" in out
        # Verify it did NOT print the tooling/allowedTools message
        assert "Auto-fixed tooling/allowedTools" not in out


class TestDoctorStt:
    """Tests for doctor Speech-to-Text section.

    STT now resolves through the typed registry: enabled lives in
    use_case_settings/stt.json (read via load_use_case_settings) and the
    active model in active_models.json (read via active_stt).
    """

    def _agent_file(self, tmp_path):
        agent_file = tmp_path / "gideon.json"
        agent_data = {
            "tools": ["@gideon-core", "@gideon-schedule"],
            "allowedTools": ["@gideon-core", "@gideon-schedule"],
            "mcpServers": {
                "gideon-core": {
                    "command": "/usr/local/bin/gideon",
                    "args": ["mcp-core"],
                },
                "gideon-schedule": {
                    "command": "/usr/local/bin/gideon",
                    "args": ["mcp-schedule"],
                },
            },
        }
        agent_file.write_text(json.dumps(agent_data))

    def test_doctor_stt_enabled_with_model(self, tmp_path, capsys):
        self._agent_file(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="gideon-cli 1.0.0", stderr="")
        provider = MagicMock()
        provider.name = "faster_whisper"
        with (
            patch(
                "gideon.interfaces.cli.doctor.shutil.which",
                side_effect=lambda b: f"/usr/local/bin/{b}",
            ),
            patch("gideon.interfaces.cli.doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("no gateway"),
            ),
            patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
            patch("gideon.interfaces.cli.doctor.ensure_ffmpeg_in_path"),
            patch(
                "gideon.extensions.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch(
                "gideon.integrations.stt.registry.active_stt",
                return_value=(provider, "turbo"),
            ),
        ):
            _doctor()
        out = capsys.readouterr().out
        assert "Speech-to-Text" in out
        assert "model:" in out
        assert "faster_whisper:turbo" in out
        assert "ffmpeg:      ✅" in out

    def test_doctor_stt_enabled_no_model(self, tmp_path, capsys):
        self._agent_file(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="gideon-cli 1.0.0", stderr="")
        with (
            patch(
                "gideon.interfaces.cli.doctor.shutil.which",
                side_effect=lambda b: f"/usr/local/bin/{b}",
            ),
            patch("gideon.interfaces.cli.doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("no gateway"),
            ),
            patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
            patch("gideon.interfaces.cli.doctor.ensure_ffmpeg_in_path"),
            patch(
                "gideon.extensions.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch(
                "gideon.integrations.stt.registry.active_stt",
                return_value=None,
            ),
        ):
            _doctor()
        out = capsys.readouterr().out
        assert "Speech-to-Text" in out
        assert "no STT model configured" in out

    def test_doctor_stt_disabled(self, tmp_path, capsys):
        self._agent_file(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="gideon-cli 1.0.0", stderr="")
        with (
            patch(
                "gideon.interfaces.cli.doctor.shutil.which",
                side_effect=lambda b: f"/usr/local/bin/{b}",
            ),
            patch("gideon.interfaces.cli.doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("no gateway"),
            ),
            patch("gideon.interfaces.cli.doctor.is_local_bind", return_value=True),
            patch("gideon.interfaces.cli.doctor.ensure_ffmpeg_in_path"),
            patch(
                "gideon.extensions.providers.use_cases.load_use_case_settings",
                return_value={"enabled": False},
            ),
            patch(
                "gideon.integrations.stt.registry.active_stt",
                return_value=None,
            ),
        ):
            _doctor()
        out = capsys.readouterr().out
        assert "Speech-to-Text" in out
        assert "disabled" in out


class TestConfigDirOverride:
    """Tests that CLI functions respect GIDEON_HOME env var via config_dir()."""

    def test_project_dir_file_uses_config_dir(self, tmp_path, monkeypatch):
        """_project_dir_file() returns path under config_dir(), not hardcoded home."""
        monkeypatch.setattr("gideon.interfaces.cli.main.config_dir", lambda: tmp_path)

        from gideon.interfaces.cli.main import _project_dir_file

        assert _project_dir_file() == tmp_path / "project_dir"

    @staticmethod
    def _make_checkout(root):
        """Materialize the markers of a real Gideon source checkout."""
        (root / "runtime" / "gideon").mkdir(parents=True)
        (root / "pyproject.toml").write_text('[project]\nname = "gideon"\n')
        return root

    def test_detect_project_dir_matches_published_repo_layout(
        self, tmp_path, monkeypatch
    ):
        """The published layout (repo root IS the package root) is detected.

        PUBL-8 drive: the previous markers were top-level ``agents/`` + ``skills/``,
        which the published repository has never had (they live at
        ``runtime/gideon/{agents,skills}``). Nothing matched, so
        GIDEON_PROJECT_DIR stayed unset and a git checkout was classified as
        a ``pip`` install — routing "Update & Restart" into a PyPI wheel upgrade.
        """
        proj = self._make_checkout(tmp_path / "Gideon")
        sub = proj / "runtime" / "gideon"
        monkeypatch.chdir(sub)

        from gideon.interfaces.cli.main import _detect_project_dir

        assert _detect_project_dir() == str(proj)

    def test_detect_project_dir_rejects_agents_skills_only_tree(
        self, tmp_path, monkeypatch
    ):
        """A bare agents/+skills/ tree is NOT a checkout — it carries no package."""
        proj = tmp_path / "not_a_checkout"
        (proj / "agents").mkdir(parents=True)
        (proj / "skills").mkdir()
        monkeypatch.chdir(proj)
        monkeypatch.setattr(
            "gideon.interfaces.cli.main.config_dir", lambda: tmp_path / "cfg"
        )

        from gideon.interfaces.cli.main import _detect_project_dir

        assert _detect_project_dir() is None

    def test_detect_project_dir_reads_from_config_dir(self, tmp_path, monkeypatch):
        """_detect_project_dir reads saved path from config_dir()/project_dir."""
        proj = self._make_checkout(tmp_path / "my_project")

        config_home = tmp_path / "custom_config"
        config_home.mkdir()
        (config_home / "project_dir").write_text(str(proj) + "\n")

        monkeypatch.setattr(
            "gideon.interfaces.cli.main.config_dir", lambda: config_home
        )
        monkeypatch.chdir(tmp_path)

        from gideon.interfaces.cli.main import _detect_project_dir

        assert _detect_project_dir() == str(proj)

    def test_logout_reads_secret_from_config_dir(self, tmp_path, monkeypatch):
        """_logout reads .local_secret from config_dir(), not ~/.gideon."""
        secret_file = tmp_path / ".local_secret"
        secret_file.write_text("test-secret")
        monkeypatch.setattr("gideon.interfaces.cli.server.config_dir", lambda: tmp_path)

        from gideon.interfaces.cli.server import _logout

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _logout(7777)


class TestHiddenCommands:
    """req.29 — internal commands dispatch but appear on no help surface.

    The tree is WALKED rather than sampled: ``mcp-core`` is registered on the top-level
    subparsers, so a check that only read ``gideon --help`` would pass while the same
    class of leak in any of the 100+ nested subcommands went unseen. Each assertion
    below runs against every node the parser can reach.
    """

    @staticmethod
    def _walk(parser, path=()):
        """Yield ``(path, help_text, subparser)`` for EVERY command in the tree."""
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            listed = {ca.dest: ca.help for ca in action._choices_actions}
            for name, sub in action._name_parser_map.items():
                yield path + (name,), listed.get(name), sub
                yield from TestHiddenCommands._walk(sub, path + (name,))

    @staticmethod
    def _rendered(parser):
        """Every help/usage string a user can reach WITHOUT already knowing the name.

        A hidden command's own ``--help`` names itself, and must: the point is that no
        surface *leads* a user to it, not that the command becomes unusable once you
        have been told it exists. So its own subtree is excluded and everything else —
        the root, and all 100+ visible nodes — is scanned.
        """
        from gideon.interfaces.cli.main import HIDDEN_COMMANDS

        surfaces = [(("gideon",), parser.format_help(), parser.format_usage())]
        for path, _help, sub in TestHiddenCommands._walk(parser):
            if any(part in HIDDEN_COMMANDS for part in path):
                continue
            surfaces.append((path, sub.format_help(), sub.format_usage()))
        return surfaces

    def test_the_tree_walk_actually_reaches_the_whole_tree(self):
        """VACUITY floor: every assertion below is only as good as this walk.

        A walker that silently stopped at depth 1 would make the rest of this class
        pass on a tree it never entered, so the reachable command count and the
        presence of a known nested leaf are pinned here.
        """
        from gideon.interfaces.cli.main import build_parser

        paths = [p for p, _h, _s in self._walk(build_parser())]
        assert len(paths) > 90, f"only {len(paths)} commands walked"
        assert ("cron", "add") in paths, "the walk never descended into a subcommand"
        assert ("spawn", "run") in paths
        assert max(len(p) for p in paths) >= 2, "no nested command was reached"

    def test_no_help_surface_names_a_hidden_command(self):
        from gideon.interfaces.cli.main import HIDDEN_COMMANDS, build_parser

        assert HIDDEN_COMMANDS, "the registry must not be empty or this is vacuous"
        for path, help_text, usage in self._rendered(build_parser()):
            for hidden in HIDDEN_COMMANDS:
                assert hidden not in help_text, f"{hidden} leaked into {path} --help"
                assert hidden not in usage, f"{hidden} leaked into {path} usage"

    def test_the_suppress_sentinel_is_never_rendered_as_text(self):
        """🔴 ``help=argparse.SUPPRESS`` printed ``mcp-core  ==SUPPRESS==`` verbatim."""
        from gideon.interfaces.cli.main import build_parser

        for path, help_text, usage in self._rendered(build_parser()):
            assert argparse.SUPPRESS not in help_text, f"sentinel in {path} --help"
            assert argparse.SUPPRESS not in usage, f"sentinel in {path} usage"

    def test_a_mistyped_command_is_not_offered_the_hidden_one(self, capsys):
        """The invalid-choice error is a command listing too."""
        from gideon.interfaces.cli.main import HIDDEN_COMMANDS, build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["definitely-not-a-command"])
        err = capsys.readouterr().err
        assert "invalid choice" in err
        assert "'chat'" in err, "the visible commands must still be offered"
        for hidden in HIDDEN_COMMANDS:
            assert hidden not in err

    def test_every_visible_command_has_a_non_empty_help_string(self):
        from gideon.interfaces.cli.main import HIDDEN_COMMANDS, build_parser

        missing = [
            path
            for path, help_text, _sub in self._walk(build_parser())
            if path[-1] not in HIDDEN_COMMANDS and not (help_text or "").strip()
        ]
        assert missing == [], f"commands with no help: {missing}"

    def test_the_hidden_commands_own_help_is_excluded_on_purpose(self):
        """VACUITY for the exclusion above: it must skip exactly one subtree.

        If ``_rendered`` dropped more than the hidden subtree, the leak assertions would
        be scanning a shrunken tree and passing for the wrong reason.
        """
        from gideon.interfaces.cli.main import build_parser

        parser = build_parser()
        walked = {path for path, _h, _s in self._walk(parser)}
        scanned = {path for path, _h, _u in self._rendered(parser)}
        assert scanned - {("gideon",)} == walked - {("mcp-core",)}

    def test_a_hidden_command_still_parses(self):
        from gideon.interfaces.cli.main import HIDDEN_COMMANDS, build_parser

        parser = build_parser()
        for hidden in HIDDEN_COMMANDS:
            assert parser.parse_args([hidden]).command == hidden

    def test_a_hidden_command_still_dispatches(self, monkeypatch):
        """Parsing is not dispatch — assert the CALL SITE runs."""
        import sys

        from gideon.interfaces.cli import main as cli

        ran = []
        monkeypatch.setattr(
            "gideon.integrations.mcp_core.run_mcp_core_server",
            lambda: ran.append("mcp-core"),
        )
        monkeypatch.setattr(sys, "argv", ["gideon", "mcp-core"])
        cli.main()
        assert ran == ["mcp-core"]

    def test_the_hiding_class_reaches_every_depth_of_the_tree(self):
        """``add_subparsers`` defaults ``parser_class`` to ``type(self)``.

        Pinned so a future nested parser built from a bare ``ArgumentParser`` cannot
        quietly reopen the invalid-choice leak below the top level.
        """
        from gideon.interfaces.cli.main import HiddenCommandParser, build_parser

        parser = build_parser()
        assert isinstance(parser, HiddenCommandParser)
        for path, _help, sub in self._walk(parser):
            assert isinstance(sub, HiddenCommandParser), path

    def test_hiding_is_a_registry_not_a_hard_coded_name(self, capsys):
        """ac_1's "mechanism" clause: adding a name must be all it takes.

        Driven by adding a second hidden command to a freshly built tree and asserting
        the same three surfaces close for it — so the mechanism is what hides, not a
        branch that happens to mention ``mcp-core``.
        """
        from gideon.interfaces.cli import main as cli

        monkey = cli.HIDDEN_COMMANDS | {"secret-probe"}
        parser = cli.HiddenCommandParser(prog="gideon")
        sub = parser.add_subparsers(dest="command")
        sub.add_parser("chat", help="Chat with the agent")
        cli.add_hidden_parser(sub, "secret-probe")
        original = cli.HIDDEN_COMMANDS
        cli.HIDDEN_COMMANDS = monkey
        try:
            cli.hide_internal_commands(sub)
            help_text = parser.format_help()
            assert "secret-probe" not in help_text
            assert argparse.SUPPRESS not in help_text
            assert parser.parse_args(["secret-probe"]).command == "secret-probe"
            with pytest.raises(SystemExit):
                parser.parse_args(["bogus"])
            assert "secret-probe" not in capsys.readouterr().err
        finally:
            cli.HIDDEN_COMMANDS = original

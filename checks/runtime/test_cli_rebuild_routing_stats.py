"""`gideon doctor --rebuild-routing-stats` — the flag `routing/stats.py` already named.

The regression pinned here is REACHABILITY, not folding. ``routing.stats.rebuild`` shipped correct
and tested with **zero non-test callers**: its own docstring called itself "the
``--rebuild-routing-stats`` maintenance path", ``routing/usage.py`` noted in as many words that no
``cli.py`` argument implemented it, and so the only recovery for a deleted ``routing_stats.json``
could not be invoked at all.

It mattered because the two folds recover differently. The usage fold self-heals — every
``GET /api/usage`` calls ``usage.refresh``, which refolds. The routing fold has no such read path:
``GET /api/models/telemetry`` calls ``load_stats`` only, and a missing file reads as an empty fold
rather than an error. So a deleted fold left the Routing & Efficiency view permanently blank and
dropped the learned policy's per-ref sample counts below its ``n >= 5`` floor, while
``model_calls.jsonl`` still held every row needed to restore both.

The assertions are split deliberately. ``TestItDispatches`` proves the flag reaches the executor —
the half that was missing, and the half a fold test cannot see, following
``test_cli_run.py``'s rule that a registered flag rendering ``--help`` proves nothing about
dispatch. ``TestItRecovers`` proves the executor does the work and stays honest about an empty
audit log.
"""

from __future__ import annotations

import json
import sys

import pytest

from gideon.engine.routing import stats
from gideon.interfaces.cli import doctor as cli_doctor
from gideon.interfaces.cli import main as cli


def _row(**over):
    """One classifiable attempt row, in the shape `AttemptRecord.to_json_line` writes."""
    row = {
        "use_case": "reasoning",
        "query_class": "summarize",
        "provider": "ollama-models",
        "model": "qwen3:8b",
        "passed": True,
        "latency_ms": 1500.0,
        "dollars_est": 0.0,
    }
    row.update(over)
    return row


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home. NEVER the real one — this writes routing_stats.json."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setattr(cli_doctor, "config_dir", lambda: tmp_path)
    return tmp_path


class TestItDispatches:
    """The half that was missing — `stats.rebuild` was already correct and already tested."""

    def test_the_flag_reaches_the_executor(self, monkeypatch):
        called: list[bool] = []
        monkeypatch.setattr(
            cli, "_doctor_rebuild_routing_stats", lambda: called.append(True)
        )
        monkeypatch.setattr(
            sys, "argv", ["gideon", "doctor", "--rebuild-routing-stats"]
        )
        cli.main()
        assert called == [
            True
        ], "cli.main did not dispatch the flag to the rebuild executor"

    def test_it_does_not_shadow_the_other_doctor_flag_or_plain_doctor(
        self, monkeypatch
    ):
        """Three mutually exclusive doctor paths. A new branch must not swallow the other two."""
        seen: list[str] = []
        monkeypatch.setattr(
            cli, "_doctor_rebuild_routing_stats", lambda: seen.append("rebuild")
        )
        monkeypatch.setattr(cli, "_doctor_paths", lambda: seen.append("paths"))
        monkeypatch.setattr(cli, "_doctor", lambda: seen.append("doctor"))

        for argv, expected in (
            (["gideon", "doctor", "--paths"], "paths"),
            (["gideon", "doctor", "--rebuild-routing-stats"], "rebuild"),
            (["gideon", "doctor"], "doctor"),
        ):
            seen.clear()
            monkeypatch.setattr(sys, "argv", argv)
            cli.main()
            assert seen == [
                expected
            ], f"{argv[1:]} dispatched {seen} instead of [{expected!r}]"


class TestItRecovers:
    def test_a_deleted_fold_is_restored_from_the_audit_log(
        self, home, monkeypatch, capsys
    ):
        audit = home / "model_calls.jsonl"
        rows = [_row(), _row(passed=False, latency_ms=2200.0), _row(use_case="chat")]
        audit.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "gideon.security.guardrails.audit._audit_path", lambda: audit
        )

        assert not (home / "routing_stats.json").exists()

        cli_doctor._doctor_rebuild_routing_stats()

        folded = stats.load_stats(home)
        assert (
            folded["use_cases"]["reasoning"]["summarize"]["ollama-models:qwen3:8b"]["n"]
            == 2
        )
        assert "chat" in folded["use_cases"]
        out = capsys.readouterr().out
        assert "3 attempt rows" in out
        assert "routing_stats.json" in out

    def test_an_empty_audit_log_says_so_rather_than_claiming_success(
        self, home, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "gideon.security.guardrails.audit._audit_path",
            lambda: home / "absent.jsonl",
        )
        cli_doctor._doctor_rebuild_routing_stats()
        out = capsys.readouterr().out
        assert "refolded 0 attempt rows" in out
        assert "the fold is empty, not broken" in out
        assert stats.load_stats(home)["use_cases"] == {}

    def test_it_overwrites_a_corrupt_fold(self, home, monkeypatch, capsys):
        (home / "routing_stats.json").write_text("{ not json", encoding="utf-8")
        audit = home / "model_calls.jsonl"
        audit.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
        monkeypatch.setattr(
            "gideon.security.guardrails.audit._audit_path", lambda: audit
        )

        cli_doctor._doctor_rebuild_routing_stats()

        assert "refolded 1 attempt row" in capsys.readouterr().out
        assert stats.load_stats(home)["use_cases"]["reasoning"]["summarize"]

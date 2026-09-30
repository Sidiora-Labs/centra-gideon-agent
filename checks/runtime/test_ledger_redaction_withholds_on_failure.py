from __future__ import annotations

from gideon.assurance.ledger import EVENTS_FILE, JOURNAL_FILE, STEP_FAILED
from gideon.automation.workflows.journal import Journal
from gideon.automation.workflows.store import RunDocuments

SECRET = "hunter2-unmaskable-pass"
LOGIN_URL = f"https://ada:{SECRET}@git.example.com/r.git"
WITHHELD = "[redaction failed; text withheld]"


def test_append_only_ledger_withholds_text_when_redaction_raises(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / ".gideon"))

    for redactor in ("redact_exfiltration_urls", "redact_credentials"):
        def broken(*_args, **_kwargs):
            raise RuntimeError(f"masking {LOGIN_URL} failed")

        with monkeypatch.context() as patcher:
            patcher.setattr(f"gideon.security.security.{redactor}", broken)
            writer = Journal(run_id=f"fail-{redactor}")
            written = writer.write(
                STEP_FAILED,
                output=LOGIN_URL,
                lines=[f"pushed to {LOGIN_URL}"],
                exit=1,
            )

        assert written["output"] == WITHHELD
        assert written["lines"] == [WITHHELD]
        documents = RunDocuments(writer.run_id)
        journal_path = documents.root / JOURNAL_FILE
        events_path = documents.root / EVENTS_FILE
        assert SECRET not in journal_path.read_text(encoding="utf-8")
        assert SECRET not in events_path.read_text(encoding="utf-8")
        assert documents.records(JOURNAL_FILE)[0]["output"] == WITHHELD
        assert documents.records(EVENTS_FILE)[0]["lines"] == [WITHHELD]

    writer = Journal(run_id="masked-ok")
    written = writer.write(STEP_FAILED, output=f"pushed to {LOGIN_URL}")
    assert SECRET not in written["output"]
    assert written["output"].startswith("pushed to https://")
    assert written["output"] != WITHHELD

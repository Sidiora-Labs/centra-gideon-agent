from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gideon.extensions.skills import ephemeral
from gideon.extensions.skills import loader as loader_mod
from gideon.extensions.skills import proposals, refine
from gideon.extensions.skills.loader import ProcedureLibrary


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "config_dir", lambda: tmp_path)
    import gideon.extensions.skills.marketplace as marketplace

    monkeypatch.setattr(marketplace, "SKILL_DISCOVERY_PATHS", [])
    return tmp_path


def test_redaction_failure_withholds_draft_and_refinement_text(home, monkeypatch):
    secret = "AKIAIOSFODNN7EXAMPLE"
    draft = ephemeral.remember(
        "normal-session", "Safe draft", f"set aws_secret_access_key={secret}"
    )
    assert draft is not None
    assert secret not in draft.body
    assert (
        secret
        not in (
            home / "skills" / ".ephemeral" / "normal-session" / "safe-draft.json"
        ).read_text()
    )

    assert ProcedureLibrary(install_builtins=False).create_skill(
        "deployment",
        "---\nname: deployment\ndescription: deployment\n---\n\nDeploy safely.\n",
    )
    proposal = refine.propose_refinement(
        trigger="correction",
        skill="deployment",
        user_message=f"Use this credential: aws_secret_access_key={secret}",
        session_key="normal-refinement",
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    assert proposal is not None
    assert secret not in proposal.procedure_md
    assert secret not in proposal.source_excerpt

    import gideon.security.security as security

    def fail_redaction(_text):
        raise RuntimeError("redaction unavailable")

    monkeypatch.setattr(security, "redact_field", fail_redaction)
    draft_secret = "draft-must-not-be-stored-7319"
    assert (
        ephemeral.remember(
            "failed-session", "Withheld draft", f"credential={draft_secret}"
        )
        is None
    )
    assert ephemeral.list_drafts("failed-session") == []
    failed_draft_dir = home / "skills" / ".ephemeral" / "failed-session"
    assert not failed_draft_dir.exists()

    quote_secret = "refinement-must-not-be-stored-8426"
    assert (
        refine.propose_refinement(
            trigger="correction",
            skill="deployment",
            user_message=f"Use this private quote: {quote_secret}",
            session_key="failed-refinement",
            now=datetime(2026, 9, 30, 0, 1, tzinfo=timezone.utc),
        )
        is None
    )
    stored_proposals = list((home / "skills" / ".proposals").glob("*.json"))
    assert len(stored_proposals) == 1
    assert quote_secret not in stored_proposals[0].read_text(encoding="utf-8")
    assert draft_secret not in "".join(
        path.read_text(encoding="utf-8") for path in home.rglob("*.json")
    )

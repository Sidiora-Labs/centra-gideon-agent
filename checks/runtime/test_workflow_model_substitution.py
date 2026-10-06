from __future__ import annotations

import json
import uuid

from gideon.automation.workflows import store
from gideon.automation.workflows.journal import JOURNAL_FILE, Journal
from gideon.automation.workflows.models import NodeInstance


def test_workflow_responder_metadata_survives_real_codec_and_journal(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    substitution = {
        "requested": "local:preferred",
        "served": "local:available",
        "why": "unavailable",
        "fix": "Check the preferred model.",
        "who": "stage model",
    }
    instance = NodeInstance(
        path="root.children[0]",
        served_model_ref="local:available",
        model_substitutions=[substitution],
    )
    decoded = NodeInstance.from_dict(json.loads(json.dumps(instance.to_dict())))
    assert decoded.served_model_ref == instance.served_model_ref
    assert decoded.model_substitutions == [substitution]
    assert NodeInstance.from_dict({"path": instance.path}).model_substitutions == []

    journal = Journal(uuid.uuid4().hex)
    journal.write(
        "model_substitution",
        instance_path=instance.path,
        served_model_ref=decoded.served_model_ref,
        model_substitutions=decoded.model_substitutions,
    )
    recorded = store.read_jsonl(journal.run_id, JOURNAL_FILE)[-1]
    assert recorded["served_model_ref"] == "local:available"
    assert recorded["model_substitutions"] == [substitution]
    _, preview = journal.store_output(instance.path, {"answer": [1, 2]})
    assert preview == {"answer": [1, 2]}

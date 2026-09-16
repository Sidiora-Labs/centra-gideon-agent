from gideon.core.config.loader import AppConfig
from gideon.engine.routing import policy, proposals


def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    config = AppConfig()
    config.routing.enabled = True
    config.save()
    policy.set_mode("chat", "heuristic")
    return config


def propose(home, **changes):
    values = dict(
        use_case="chat",
        query_class="short_chat",
        current=["a:one", "b:two"],
        proposed=["b:two", "a:one"],
        evidence={"n": 12},
        home=home,
    )
    values.update(changes)
    result = proposals.propose(**values)
    assert result is not None
    return result


def test_saved_controls_precedence_preserves_candidate_multiplicity(
    tmp_path, monkeypatch
):
    configured(tmp_path, monkeypatch)
    refs = ["a:one", "b:two", "a:one", "c:new"]
    policy.set_order("chat", "short_chat", ["b:two", "a:one"], home=tmp_path)
    assert policy.route_refs("chat", "short_chat", refs, home=tmp_path) == [
        "b:two",
        "a:one",
        "a:one",
        "c:new",
    ]
    policy.set_pin("chat", "c:new")
    assert policy.route_refs("chat", "short_chat", refs, home=tmp_path) == [
        "c:new",
        "a:one",
        "b:two",
        "a:one",
    ]
    policy.set_mode("chat", "off")
    assert policy.route_refs("chat", "short_chat", refs, home=tmp_path) == refs
    policy.set_pin("chat", "")
    assert policy.pin_for("chat", home=tmp_path) == ""
    assert policy.table_for("chat", home=tmp_path)["classes"]["short_chat"][
        "basis"
    ] == {"source": "user"}


def test_acceptance_stands_after_real_queue_write_failure(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch)
    record = propose(tmp_path)
    decision = proposals.ProposalDecision.open(record.id, tmp_path)
    path = tmp_path / "routing_proposals.json"
    path.unlink()
    path.mkdir()
    assert decision.accept() is True
    assert policy.table_order("chat", "short_chat", home=tmp_path) == record.proposed
    assert (
        policy.order_basis("chat", "short_chat", home=tmp_path)["proposal_id"]
        == record.id
    )
    assert path.is_dir()


def test_rejection_reports_real_queue_write_failure_without_policy_mutation(
    tmp_path, monkeypatch
):
    configured(tmp_path, monkeypatch)
    policy.save_policy(tmp_path, policy._empty_policy())
    before = (tmp_path / "routing_policy.json").read_bytes()
    record = propose(tmp_path)
    decision = proposals.ProposalDecision.open(record.id, tmp_path)
    path = tmp_path / "routing_proposals.json"
    path.unlink()
    path.mkdir()
    assert decision.reject() is False
    assert (tmp_path / "routing_policy.json").read_bytes() == before


def test_hand_set_order_refusal_is_durable_and_evidence_is_fenced(
    tmp_path, monkeypatch
):
    configured(tmp_path, monkeypatch)
    policy.set_order("chat", "short_chat", ["a:one", "b:two"], home=tmp_path)
    record = propose(
        tmp_path,
        evidence={
            "note": "untrusted instruction",
            "sample_audit_ids": [str(n) for n in range(25)],
        },
    )
    assert not proposals.accept(record.id, home=tmp_path)
    saved = proposals.find(record.id, home=tmp_path)
    assert saved.status == "refused" and saved.decided_at and saved.refusal_reason
    assert "routing-telemetry" in saved.evidence["note"]
    assert len(saved.evidence["sample_audit_ids"]) == 20
    assert proposals.pending(home=tmp_path) == []
    assert policy.table_order("chat", "short_chat", home=tmp_path) == record.current

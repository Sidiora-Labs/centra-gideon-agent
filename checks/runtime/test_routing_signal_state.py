import json

from gideon.automation.workflows.journal import Journal
from gideon.core.config.loader import AppConfig
from gideon.engine.routing import (
    classifier,
    feedback,
    gap,
    learned,
    policy,
    proposals,
    stats,
)


def configure(home, monkeypatch, mode):
    monkeypatch.setenv("GIDEON_HOME", str(home))
    config = AppConfig()
    config.routing.enabled = True
    config.save()
    table = policy._empty_policy()
    table["use_cases"]["chat"] = {"mode": mode}
    policy.save_policy(home, table)
    assert policy.master_enabled()
    assert policy.mode_for("chat", home=home) == mode


def fold(home, rates):
    document = {"version": stats.STATS_VERSION, "use_cases": {}}
    for ref, success in rates.items():
        provider, model = ref.split(":", 1)
        for index in range(6):
            stats.fold_record(
                document,
                {
                    "use_case": "chat",
                    "query_class": "short_chat",
                    "provider": provider,
                    "model": model,
                    "passed": success,
                    "latency_ms": 100.0,
                    "dollars_est": 0.0,
                },
            )
    stats.save_stats(home, document)
    return document


def test_real_journal_feedback_changes_live_policy_order(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, "learned")
    refs = ["alpha:one", "beta:two"]
    fold(tmp_path, dict.fromkeys(refs, True))
    assert policy.route_refs("chat", "short_chat", refs, home=tmp_path) == refs
    record = Journal("routing-observation").write(
        "judge_verdict",
        use_case="chat",
        query_class="short_chat",
        ref=refs[0],
        verdict="REJECT",
    )
    assert record["event_id"]
    assert feedback.feedback_for("chat", "short_chat", refs[0], home=tmp_path) == (
        0.0,
        1,
    )
    assert policy.route_refs("chat", "short_chat", refs, home=tmp_path) == refs[::-1]


def test_real_gap_queue_requires_acceptance_before_policy_changes(
    tmp_path, monkeypatch
):
    configure(tmp_path, monkeypatch, "heuristic")
    state = fold(tmp_path, {"alpha:one": False, "beta:two": True})
    path = tmp_path / "routing_policy.json"
    before = path.read_bytes()
    proposal = gap.detect_gap(state, "chat", "short_chat", home=tmp_path)
    assert proposal is not None
    assert proposal.proposed == ["beta:two", "alpha:one"]
    assert path.read_bytes() == before
    assert gap.detect_gap(state, "chat", "short_chat", home=tmp_path) is None
    assert proposals.accept(proposal.id, home=tmp_path)
    assert path.read_bytes() != before
    assert policy.table_order("chat", "short_chat", home=tmp_path) == proposal.proposed


def test_rejected_observation_does_not_consume_its_event_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    writer = Journal("identity-replay")
    skipped = writer.write("judge_verdict", verdict="PASS")
    path = tmp_path / "workflows" / "runs" / "identity-replay" / "events.jsonl"
    accepted = {
        **skipped,
        "use_case": "chat",
        "query_class": "short_chat",
        "ref": "local:one",
    }
    with path.open("a") as stream:
        stream.write(json.dumps(accepted) + "\n" + json.dumps(accepted) + "\n{broken")
    assert feedback.feedback_index(home=tmp_path) == {
        ("chat", "short_chat", "local:one"): (1.0, 1)
    }


def test_unknown_cost_and_unmeasured_candidate_keep_separate_slots(tmp_path):
    refs = ["local:a", "local:new", "local:b", "local:c"]
    document = fold(tmp_path, {refs[0]: True, refs[2]: True, refs[3]: True})
    prices = {refs[0]: 9.0, refs[3]: 1.0}
    assert learned.learned_order(
        refs,
        use_case="chat",
        query_class=classifier.classify_query("hello"),
        stats=document,
        hysteresis=0.05,
        cloud_quality_margin=0.1,
        local_keys={"local"},
        cost_of=prices.__getitem__,
    ) == ["local:c", "local:new", "local:b", "local:a"]

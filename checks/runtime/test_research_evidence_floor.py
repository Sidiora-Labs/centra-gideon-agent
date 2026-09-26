"""Readable-page coverage must come from successful fetches, not finding claims."""

from __future__ import annotations

import asyncio

from gideon.automation.loop import files, store, supervisor
from gideon.automation.loop.loop import Loop
from gideon.automation.loop.research_sources import coverage, record, unmet_reason
from gideon.automation.workflows.supervisor_policy import policy_for_kind


def test_research_completion_waits_for_distinct_readable_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "config_dir", lambda: tmp_path)
    loop = store.create(
        Loop(
            id="",
            name="Evidence review",
            kind="research",
            task="Compare two public methods",
            kind_config={"goal_type": "open_ended", "evidence_min_pages": 2, "evidence_min_domains": 2},
        )
    )
    key = f"loop-{loop.id}"
    record(key, "https://a.example/report#part", 1000)
    record(key, "https://a.example/report#again", 1000)
    record(key, "https://b.example/listing", 20)
    assert coverage(loop.id) == {"pages": 1, "domains": 1}
    assert "1/2 distinct pages and 1/2 sites" in unmet_reason(loop.id, loop.kind_config)
    policy = policy_for_kind(loop.kind, loop.kind_config)
    assert asyncio.run(supervisor.done_signal(loop, [{"cycle": 1}], policy)) is False
    assert "1/2" in files.read_guidance(loop.id)

    record(key, "https://b.example/article", 1000)
    assert coverage(loop.id) == {"pages": 2, "domains": 2}
    assert unmet_reason(loop.id, loop.kind_config) is None

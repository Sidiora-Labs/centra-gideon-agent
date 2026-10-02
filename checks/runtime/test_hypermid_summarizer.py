from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from gideon.hypermid.models import Cursor, Scope
from gideon.hypermid.summarizer import (
    SummaryJob,
    model_provider_authority,
    summarize_job,
)
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider


pytestmark = pytest.mark.skipif(
    os.environ.get("HYPERMID_REAL_MODEL_TEST") != "1",
    reason="requires an explicitly enabled real model call",
)


@pytest.mark.asyncio
async def test_real_bounded_summary_call_records_usage_without_serializing_credentials(
    tmp_path, monkeypatch
):
    endpoint = os.environ["GATEWAY_ROUTER"]
    secret = os.environ["GATEWAY_ROUTER_API_KEY"]
    model = os.environ["HYPERMID_TEST_MODEL"]
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))

    source_items = [
        {
            "item_id": "item-1",
            "role": "user",
            "content": "We selected SQLite for the first release and require atomic writes.",
        },
        {
            "item_id": "item-2",
            "role": "assistant",
            "content": "The next action is to verify crash recovery before publication.",
        },
    ]
    source_digest = hashlib.sha256(
        json.dumps(source_items, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    now_ms = int(time.time() * 1000)
    job = SummaryJob(
        job_id="job-real-1",
        scope=Scope(owner_id="owner-1", project_id="project-1"),
        session_id="session-real-1",
        source_start=Cursor(epoch=1, sequence=1),
        source_end=Cursor(epoch=1, sequence=2),
        source_digest=source_digest,
        lease_id="lease-real-1",
        lease_expires_at_ms=now_ms + 120_000,
        tier_levels=(0, 1, 2, 3),
        locale="en-US",
        max_input_tokens=256,
        max_output_tokens=256,
        attempt=1,
    )
    provider = OpenAIProvider(
        model=model,
        credential=Credential(
            name="hypermid-live-test", kind="api_key", secret=secret, source="env"
        ),
        base_url=endpoint,
        max_tokens=job.max_output_tokens,
        extra_options={"temperature": 0},
    )
    provider.served_model_ref = f"centra:{model}"
    try:
        candidate = await summarize_job(
            job,
            source_items,
            source_token_count=64,
            now_ms=now_ms,
            timeout_seconds=90,
            authority=model_provider_authority(provider),
        )
    finally:
        await provider.shutdown()

    assert [tier.level for tier in candidate.tiers] == [0, 1, 2, 3]
    assert all(tier.content and tier.token_mass > 0 for tier in candidate.tiers)
    assert candidate.source_digest == source_digest
    assert candidate.usage.output_tokens is not None
    assert candidate.usage.output_tokens <= job.max_output_tokens
    serialized = json.dumps(candidate.to_wire(), sort_keys=True)
    assert secret not in serialized

    usage_path = tmp_path / "gideon-home" / "usage" / "turns.jsonl"
    rows = [json.loads(line) for line in usage_path.read_text().splitlines()]
    assert rows[-1]["source"] == "background"
    assert rows[-1]["agent"] == "hypermid-summary"
    assert rows[-1]["session_key"] == job.session_id

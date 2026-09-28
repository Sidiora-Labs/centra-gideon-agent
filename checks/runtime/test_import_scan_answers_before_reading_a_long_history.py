"""A long real transcript listing answers from a prompt look, then reads in the background."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Iterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

FILES = 1200


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from gideon.core.config.loader import config_dir
    from gideon.cognition.onboarding_import.sources import claude_code, codex
    from gideon.cognition.onboarding_import.sources.common import READINGS

    home = tmp_path / "gideon-home"
    home.mkdir()
    (tmp_path / "home").mkdir()
    claude = tmp_path / "claude"
    claude.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_SKIP_SKILL_SEED", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    assert config_dir() == home
    assert claude_code.resolve_root() == claude
    assert codex.resolve_root() == tmp_path / "no-codex"
    READINGS.clear()

    for number in range(FILES):
        folder = claude / "projects" / f"project-{number % 20}"
        folder.mkdir(parents=True, exist_ok=True)
        base = {"cwd": f"/workspace/project-{number % 20}", "sessionId": f"session-{number}"}
        prompt = {**base, "type": "user", "timestamp": "2026-09-01T10:00:00Z",
                  "message": {"role": "user", "content": f"Why is build {number} slow?"}}
        reply = {**base, "type": "assistant", "timestamp": "2026-09-01T10:00:02Z",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Build output " * 300}]}}
        tool = {**base, "type": "user", "timestamp": "2026-09-01T10:00:03Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "x" * 900}]}}
        rows = [prompt, reply, *([tool] * 20)]
        path = folder / f"{number:08d}-0000-4000-8000-000000000000.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    yield home
    READINGS.clear()


@pytest.mark.asyncio
async def test_first_answer_lists_a_long_history_before_full_transcript_reads(machine: Path):
    from gideon.interfaces.dashboard.handlers.onboarding_import import register_onboarding_import_routes

    app = web.Application()
    register_onboarding_import_routes(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/onboarding/import")
        assert response.status == 200
        body = await response.json()
        claude = next(source for source in body["sources"] if source["source"] == "claude_code")
        conversations = [item for item in claude["items"] if item["category"] == "conversations"]

        assert len(conversations) == FILES
        assert all(item["provisional"] for item in conversations)
        assert all("payload" not in item and "messages" not in item for item in conversations)
        assert claude["reading"] == {"read": 0, "of": FILES}
        assert body["reading"]["of"] == FILES

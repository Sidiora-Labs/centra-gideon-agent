"""Actual process and filesystem paths for the engine's infrastructure adapters."""

import asyncio
import json
import sys
import uuid

import pytest

from gideon.engine import subagent_persistence as persistence
from gideon.engine import tmux_substrate as tmux


def test_real_tmux_round_trip_preserves_workspace_and_environment(tmp_path):
    if not tmux.tmux_available():
        pytest.skip("tmux executable unavailable")
    name = tmux.durable_session_name("rewrite", uuid.uuid4().hex, "roundtrip")
    marker = tmp_path / "ready.json"
    command = [
        sys.executable,
        "-c",
        "import json,os,time; open('ready.json','w').write(json.dumps([os.getcwd(),os.environ['GIDEON_PROCESS_VECTOR']])); time.sleep(30)",
    ]

    async def exercise():
        try:
            assert await tmux.new_session(
                name,
                workspace=str(tmp_path),
                command=command,
                env={"GIDEON_PROCESS_VECTOR": "literal spaces ; $value"},
            )
            async with asyncio.timeout(5):
                while not marker.exists():
                    await asyncio.sleep(0.02)
            assert json.loads(marker.read_text()) == [
                str(tmp_path),
                "literal spaces ; $value",
            ]
            assert await tmux.has_session(name)
            assert tmux.has_session_sync(name)
            assert name in await tmux.list_sessions()
            assert (name, str(tmp_path)) in tmux.pane_paths_sync()
        finally:
            await tmux.kill_session(name)
        async with asyncio.timeout(5):
            while await tmux.has_session(name):
                await asyncio.sleep(0.02)
        assert not tmux.has_session_sync(name)

    asyncio.run(exercise())


def test_agent_files_follow_home_changes_and_prune_owned_transcripts(
    tmp_path, monkeypatch
):
    first, second = tmp_path / "one", tmp_path / "two"
    monkeypatch.setenv("GIDEON_HOME", str(first))
    folder = persistence.create_agent_folder("worker", task="first home")
    persistence.write_result_chunk("worker", "hello")
    persistence.write_result_chunk("worker", " world")
    persistence.update_state("worker", session_id="owned")
    sessions = first / "sessions"
    sessions.mkdir()
    for suffix in (".json", ".jsonl"):
        (sessions / f"owned{suffix}").write_text("{}")
    (sessions / "unrelated.json").write_text("keep")
    persistence.write_tombstone(
        "worker", cause="stopped", recovery_action="inspect", died=1
    )
    assert json.loads((folder / "tombstone.json").read_text())["result_available"]
    assert (folder / "result.txt").read_text() == "hello world"
    monkeypatch.setenv("GIDEON_HOME", str(second))
    assert persistence.read_state("worker") is None
    other = persistence.create_agent_folder("worker", task="second home")
    assert persistence.list_orphans()[0]["task"] == "second home"
    monkeypatch.setenv("GIDEON_HOME", str(first))
    assert persistence.prune_stale_tombstones() == 1
    assert not folder.exists() and other.exists()
    assert not (sessions / "owned.json").exists()
    assert not (sessions / "owned.jsonl").exists()
    assert (sessions / "unrelated.json").read_text() == "keep"


def test_agent_path_rejects_a_real_symlink_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    outside = tmp_path / "outside"
    outside.mkdir()
    root = persistence._subagents_dir()
    root.mkdir(parents=True)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Path traversal blocked"):
        persistence.create_agent_folder("linked", task="escape")
    assert list(outside.iterdir()) == []

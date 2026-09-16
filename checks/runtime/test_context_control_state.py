import json
import math
import os
import time

import pytest

from gideon.cognition import (
    bg_compress,
)
from gideon.cognition import context_compaction as compaction
from gideon.cognition import context_management as limits
from gideon.cognition.context_headroom import (
    Component,
    Headroom,
    HeadroomState,
    Window,
    check,
)
from gideon.cognition.context_segmentation import _cosine, segment_messages
from gideon.cognition.history import ConversationLog, _archive_dir
from gideon.core.config.loader import AppConfig
from gideon.engine.subagent import SubagentInfo


@pytest.fixture
def control_home(tmp_path, monkeypatch):
    home = tmp_path / "context-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    return home


def test_real_result_files_preserve_head_tail_and_existing_character_policy(tmp_path):
    assert limits.cap_result_file(tmp_path / "absent.md") is False
    small = tmp_path / "small.md"
    small.write_text("small")
    assert limits.cap_result_file(small) is False
    assert small.read_text() == "small"
    large = tmp_path / "large.md"
    body = "H" * 200_000 + "M" * 350_000 + "T" * 450_000
    large.write_text(body)
    assert limits.cap_result_file(large) is True
    head = limits.RESULT_FILE_MAX_BYTES // 5
    tail = limits.RESULT_FILE_MAX_BYTES - head - 100
    assert (
        large.read_text()
        == body[:head] + "\n\n[...truncated 488,000 bytes...]\n\n" + body[-tail:]
    )
    unicode = tmp_path / "unicode.md"
    unicode.write_text("界" * 300_000)
    assert limits.cap_result_file(unicode) is True
    assert unicode.stat().st_size > limits.RESULT_FILE_MAX_BYTES
    assert "388,000 bytes" in unicode.read_text()


def test_in_memory_caps_retain_short_identity_and_recent_objects():
    short = [{"i": 1}]
    assert limits.cap_history(short) is short
    source = [{"i": index} for index in range(limits.HISTORY_MAX_ENTRIES + 3)]
    clipped = limits.cap_history(source)
    assert len(clipped) == limits.HISTORY_MAX_ENTRIES
    assert clipped[0] is source[3] and clipped[-1] is source[-1]
    assert limits.cap_streaming_text("short") == "short"
    stream = "old " * 20_000 + "TAIL"
    result = limits.cap_streaming_text(stream)
    assert result == "…(truncated)\n" + stream[-limits.STREAMING_TEXT_MAX_CHARS + 20 :]


def test_real_workspace_budget_counts_only_matching_files(tmp_path):
    (tmp_path / "other.md").write_bytes(b"x" * (limits.SESSION_MAX_BYTES + 1))
    (tmp_path / "agent-directory.md").mkdir()
    assert limits.check_session_budget(tmp_path) is False
    target = tmp_path / "agent-owned.md"
    with target.open("wb") as stream:
        stream.truncate(limits.SESSION_MAX_BYTES)
    assert limits.check_session_budget(tmp_path) is False
    with target.open("ab") as stream:
        stream.write(b"x")
    assert limits.check_session_budget(tmp_path) is True


def test_real_subagent_records_evict_oldest_completed_with_stable_ties():
    records = {
        "running": SubagentInfo(id="running", task="active", started=0),
        "first": SubagentInfo(id="first", task="done", started=1, done=True),
        "second": SubagentInfo(id="second", task="done", started=1, done=True),
        "recent": SubagentInfo(id="recent", task="done", started=9, done=True),
    }
    assert limits.evict_completed_agents(records, max_retained=2) == 1
    assert list(records) == ["running", "second", "recent"]
    assert limits.evict_completed_agents(records, max_retained=0) == 2
    assert list(records) == ["running"]


def test_stale_workspace_retention_reads_real_child_timestamps(control_home):
    root = control_home / "sessions"
    root.mkdir()
    expired, fresh, empty = (root / name for name in ("expired", "fresh", "empty"))
    for directory in (expired, fresh, empty):
        directory.mkdir()
    (expired / "output.md").write_text("old")
    (fresh / "output.md").write_text("current")
    old = time.time() - limits.SESSION_MAX_AGE_SECS - 100
    os.utime(expired / "output.md", (old, old))
    os.utime(empty, (old, old))
    assert limits.cleanup_stale_sessions() == 2
    assert not expired.exists() and not empty.exists() and fresh.exists()
    assert limits.cleanup_stale_sessions() == 0


def test_segment_boundaries_cover_every_original_row_and_keep_object_identity():
    messages = [{"role": "system", "content": "opening"}]
    for index in range(5):
        messages.extend(
            [
                {"role": "user", "content": str(index)},
                {"role": "tool", "content": "result"},
            ]
        )
    pieces = segment_messages(messages, turns_per_segment=2)
    assert [(part.start, part.end) for part in pieces] == [(0, 5), (5, 9), (9, 11)]
    flattened = [message for piece in pieces for message in piece.messages]
    assert all(left is right for left, right in zip(flattened, messages))
    assert len(segment_messages(messages, turns_per_segment=0)) == 5
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0
    assert _cosine([0.0], [1.0]) == 0
    assert _cosine([1.0, 1.0], [1.0]) == pytest.approx(1 / math.sqrt(2))


def test_tool_pruning_retains_handles_and_original_rows():
    plain = [
        {"role": "tool", "tool_call_id": str(index), "content": letter * 700}
        for index, letter in enumerate("AB")
    ]
    handle = 'tool_result_get(result_id="r_owned")'
    projected = {
        "role": "tool",
        "tool_call_id": "projected",
        "content": "C" * 700 + handle,
    }
    recent = [{"role": "tool", "content": "full" * 400} for _ in range(4)]
    source = plain + [projected] + recent
    result = compaction.prune_tool_outputs(source)
    assert result[0]["content"] == "[pruned tool result — 1 lines, 700 chars]"
    assert result[1]["content"] == "[pruned tool result — identical to previous]"
    assert handle in result[2]["content"]
    assert source[0]["content"] == "A" * 700
    assert all(result[-4 + index] is recent[index] for index in range(4))


def _call_pair(name, identifier, result):
    return [
        {
            "role": "assistant",
            "content": name,
            "tool_calls": [
                {"id": identifier, "function": {"name": name, "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": identifier, "content": result},
    ]


def test_compaction_carries_old_account_and_derives_failure_from_original_result():
    previous = compaction._derive_account_for(_call_pair("read_file", "old", "done"))
    assert previous
    carried = {"role": "user", "content": previous}
    messages = [{"role": "user", "content": f"head {index}"} for index in range(3)] + [
        carried
    ]
    messages += _call_pair(
        "shell", "failed", "Error: owned command failed\n" + "trace\n" * 300
    )
    for index in range(6):
        messages += _call_pair("read_file", f"later{index}", "line\n" * 200)
    tail = [{"role": "user", "content": f"latest {index}"} for index in range(8)]
    messages += tail
    after = compaction.compact(messages)
    assert after[:3] == messages[:3] and after[-8:] == tail
    assert any(row is carried for row in after)
    accounts = [
        row["content"] for row in after if row["content"].startswith("[RESUME ACCOUNT")
    ]
    assert len(accounts) == 2
    assert any(
        "shell" in line and "FAILED" in line for line in accounts[-1].splitlines()
    )


def test_file_references_keep_argument_order_and_json_shape_errors():
    messages = [
        {
            "role": "assistant",
            "content": "inspect third/c.py and first/a.py",
            "tool_calls": [
                {
                    "function": {
                        "arguments": json.dumps(
                            {"path": "first/a.py", "file_path": "second/b.py"}
                        )
                    }
                }
            ],
        }
    ]
    assert compaction.extract_file_refs(messages) == [
        "first/a.py",
        "second/b.py",
        "third/c.py",
    ]
    assert compaction.extract_file_refs(messages, limit=2) == [
        "first/a.py",
        "second/b.py",
    ]
    with pytest.raises(AttributeError):
        compaction.extract_file_refs(
            [{"tool_calls": [{"function": {"arguments": "[]"}}]}]
        )


def test_duplicate_headroom_labels_keep_separate_compression_records():
    components = [
        Component("same label", "tok " * 6000),
        Component("same label", "tok " * 5000),
        Component("request", "continue", False),
    ]
    window = Window(4000, 2000, 2000, "catalog")
    verdict = check(components, window=window)
    assert verdict.state is HeadroomState.FITS_AFTER_COMPRESSION
    assert len(verdict.compressed) == 2
    assert all(record.name == "same label" for record in verdict.compressed)
    assert verdict.raw_tokens - verdict.assembled_tokens == sum(
        record.tokens_saved for record in verdict.compressed
    )
    assert verdict.text.endswith("continue")
    assert components[0].text == "tok " * 6000
    serialized = verdict.to_dict()
    assert list(serialized) == [
        "state",
        "window",
        "assembled_tokens",
        "raw_tokens",
        "headroom_tokens",
        "pressure",
        "level",
        "compressed",
        "oversized",
        "reason",
        "fix",
    ]
    assert "2 components were compressed" in verdict.notice()


def test_zero_room_and_unknown_remain_distinct_declared_values():
    unknown = check(
        [Component("request", "hello", False)],
        window=Window(None, 100, None, "unknown"),
    )
    full = check(
        [Component("request", "hello", False)], window=Window(100, 100, 0, "catalog")
    )
    assert unknown.state is HeadroomState.FITS and unknown.window.measured is False
    assert full.state is HeadroomState.CANNOT_FIT and full.window.measured is True
    assert full.text == "" and full.headroom_tokens < 0
    assert full.pressure is None and full.level == "unmeasured"
    oversized = full.to_dict()["oversized"][0]
    assert oversized["name"] == "request" and oversized["note"] == "not compressible"


def _tool_heavy_journal(log, key):
    for index in range(24):
        log.append(key, "user", f"q{index}")
        log.append(key, "assistant", f"a{index}")
        handle = ' tool_result_get(result_id="r_preserved")' if index == 0 else ""
        log.append(key, "tool", "output\n" * 160 + handle)
    return log.read_messages(key)


@pytest.mark.asyncio
async def test_actual_background_compression_archives_without_model_calls(control_home):
    log = ConversationLog(control_home / "sessions")
    before = _tool_heavy_journal(log, "owned")
    segments = segment_messages(before)
    assert len(segments) == 3
    prose = "\n".join(
        f"{row['role']}: {row['content']}"
        for row in segments[0].messages
        if row["role"] in ("user", "assistant")
    )
    assert len(prose) < 2000
    result = await bg_compress.compress_session(log, "owned")
    assert result and result["chars_out"] < result["chars_in"]
    after = log.read_messages("owned")
    assert after[-len(segments[-1].messages) :] == segments[-1].messages
    assert "r_preserved" in after[0]["content"]
    archives = list(_archive_dir(log._dir).glob("*.jsonl"))
    assert len(archives) == 1
    archived = [json.loads(line) for line in archives[0].read_text().splitlines()]
    assert archived[0]["reason"] == "bg_compress"
    assert any(row.get("role") == "tool" for row in archived[1:])
    ledger = json.loads((control_home / "tokenjuice_savings.json").read_text())
    row = next(
        value for value in ledger["rows"].values() if value["compressor"] == "bg_topic"
    )
    assert (
        row["chars_in"] == result["chars_in"]
        and row["chars_out"] == result["chars_out"]
    )


@pytest.mark.asyncio
async def test_background_pass_selects_oldest_persistent_and_keeps_minimum_one_budget(
    control_home,
):
    config = AppConfig.load()
    config.tools.bg_compress_enabled = True
    config.tools.bg_compress_idle_days = 1
    config.save()
    log = ConversationLog(control_home / "sessions")
    old = time.time() - 20 * 86400
    for index, key in enumerate(("first", "second", "private")):
        _tool_heavy_journal(log, key)
        if key == "private":
            log.update_metadata(key, {"memory_mode": "temporary"})
        os.utime(log._path(key), (old + index, old + index))
    assert bg_compress._eligible_keys(log, 1, time.time()) == ["first", "second"]
    results = await bg_compress.run_bg_compression_pass(log, max_sessions=0)
    assert [result["key"] for result in results] == ["first"]
    assert len(log.read_messages("second")) == 72
    config.tools.bg_compress_enabled = False
    config.save()
    assert await bg_compress.run_bg_compression_pass(log) == []
    assert await bg_compress.run_bg_compression_pass(None) == []

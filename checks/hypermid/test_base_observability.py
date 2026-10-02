from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from gideon.hypermid.observability import (
    CaptureSink,
    DailySegmentWriter,
    LogFilter,
    LogLevel,
    LogParseError,
    LogRecord,
    RedactionPolicy,
    SinkHealth,
    WakeToken,
    format_line,
    parse_line,
    redact_record,
)


def test_logging_is_redacted_parseable_bounded_and_multiwriter_safe(tmp_path, capsys):
    filters = LogFilter.parse("hypermid=warn,hypermid.cache=debug,broken")
    assert filters.level_for("hypermid.cache.store") == LogLevel.DEBUG
    assert filters.level_for("other") == LogLevel.INFO
    assert filters.malformed is True

    raw = LogRecord(
        timestamp="2026-10-02T10:00:00Z",
        level=LogLevel.INFO,
        logger="hypermid.cache",
        message="Bearer abcdefghijklmnop secret-value\x1b[31mRED\x1b[0m\nforged",
        bound=(("trace_id", "secret-value"), ("authorization", "Bearer hidden123")),
        fields={"api_key": "hidden", "nested": {"text": "token=abcdefghijk"}},
    )
    clean = redact_record(
        raw,
        RedactionPolicy(("secret-value",)),
        lambda value: value.replace("RED", "module"),
    )
    line = format_line(clean)
    assert b"secret-value" not in line
    assert b"abcdefghijklmnop" not in line
    assert b"hidden" not in line
    assert b"\x1b" not in line
    assert b"\\\\u000a" in line
    assert parse_line(line) == clean
    with pytest.raises(LogParseError, match="invalid_json"):
        parse_line(b"not-json\n")

    segments = DailySegmentWriter(tmp_path / "segments", maximum_bytes=100_000)
    lines = [
        format_line(
            LogRecord(
                timestamp="2026-10-02T10:00:00Z",
                level=LogLevel.INFO,
                logger="hypermid.worker",
                message=f"line-{index}",
            )
        )
        for index in range(64)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda item: segments.append("2026-10-02", item), lines))
    stored = (tmp_path / "segments" / "hypermid-2026-10-02.log").read_bytes().splitlines()
    assert len(stored) == 64
    assert {json.loads(item)["message"] for item in stored} == {
        f"line-{index}" for index in range(64)
    }
    (tmp_path / "segments" / "hypermid-2026-09-01.log").write_text("old\n")
    (tmp_path / "segments" / "keep.txt").write_text("unrelated")
    assert segments.prune_before("2026-10-01") == (
        tmp_path / "segments" / "hypermid-2026-09-01.log",
    )

    capture = CaptureSink(tmp_path / "capture.log", maximum_bytes=8, generations=2)
    capture.write_line(b"one\n")
    capture.write_line(b"two\n")
    capture.write_line(b"three\n")
    assert (tmp_path / "capture.log").read_bytes() == b"three\n"
    assert Path(f"{tmp_path / 'capture.log'}.1").read_bytes() == b"one\ntwo\n"
    health = SinkHealth()
    health.record_failure()
    health.record_failure()
    assert health.swallowed_writes == 2
    assert capsys.readouterr().err.count("fallback") == 1

    assert WakeToken("abcdefghijklmnop").plaintext() == b"abcdefghijklmnop"
    with pytest.raises(ValueError):
        WakeToken('{"command":"run"}')

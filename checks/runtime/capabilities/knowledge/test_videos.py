import asyncio
import json
from pathlib import Path
from threading import Event

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.capabilities_knowledge_videos import register
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.net import EgressPolicy, fetch
from gideon.workspace.capabilities.knowledge.capture import CaptureError
from gideon.workspace.capabilities.knowledge.transcript_format import (
    preview,
    seconds,
    segments,
    stamp,
    video_url,
)
from gideon.workspace.capabilities.knowledge.video_fetch import (
    VideoCancelled,
    VideoReader,
)
from gideon.workspace.capabilities.knowledge.videos import VideoIngests

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
First &amp; grounded <i>caption</i>

00:01:02.000 --> 00:01:05.000 align:start
Second caption
"""
URL = "https://youtu.be/dQw4w9WgXcQ"


@pytest.fixture
def service(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    path = home / "workspace" / "knowledge" / "knowledge.db"
    path.parent.mkdir(parents=True)
    store = KnowledgeStore(str(path))
    result = VideoIngests(store, home)
    yield result
    store.close()


def supplied(request_id="video-request-0001", **changes):
    body = {
        "url": URL,
        "title": "A useful public talk",
        "format": "vtt",
        "content": VTT,
        "language": "en",
    }
    reviewed = preview(body)
    result = {"request_id": request_id, **body, "preview_id": reviewed["preview_id"]}
    result.update(changes)
    return result


@pytest.mark.parametrize(
    "value,identity",
    [
        (URL, "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_video_url_normalizes_single_https_video(value, identity):
    assert video_url(value) == (identity, "https://www.youtube.com/watch?v=" + identity)


@pytest.mark.parametrize(
    "value",
    [
        "http://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/not-eleven",
        "https://youtu.be/dQw4w9WgXcQ/other",
        "https://youtube.com/playlist?list=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=firstfirst1&v=secondsecon",
        "https://user:secret@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:444/watch?v=dQw4w9WgXcQ",
        "https://example.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_video_url_rejects_unsafe_or_ambiguous_sources(value):
    with pytest.raises(CaptureError, match="single HTTPS YouTube"):
        video_url(value)


@pytest.mark.parametrize(
    "value,expected", [(0, 0.0), ("1.25", 1.25), (60, 60.0), (86400, 86400.0)]
)
def test_seconds_accepts_finite_bounded_values(value, expected):
    assert seconds(value) == expected


@pytest.mark.parametrize(
    "value", [True, False, -1, 86400.1, "nan", "inf", None, object()]
)
def test_seconds_rejects_invalid_values(value):
    with pytest.raises(CaptureError):
        seconds(value)


@pytest.mark.parametrize(
    "value,expected",
    [("00:01.500", 1.5), ("01:02.250", 62.25), ("01:02:03.000", 3723.0)],
)
def test_stamp_parses_supported_caption_clocks(value, expected):
    assert stamp(value) == expected


@pytest.mark.parametrize(
    "value", ["1", "00:60.000", "00:00:60.000", "00:00:00:01", "word:time"]
)
def test_stamp_rejects_malformed_clocks(value):
    with pytest.raises(CaptureError):
        stamp(value)


def test_vtt_segments_preserve_timestamp_links_and_clean_markup():
    rows = segments(VTT, "vtt", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert rows == [
        {
            "start": 1.0,
            "end": 3.5,
            "text": "First & grounded caption",
            "source_link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s",
        },
        {
            "start": 62.0,
            "end": 65.0,
            "text": "Second caption",
            "source_link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=62s",
        },
    ]


def test_srt_segments_accept_commas_and_sequence_numbers():
    content = "1\n00:00:02,100 --> 00:00:03,200\nOne\n\n2\n00:00:04,000 --> 00:00:05,000\nTwo\n"
    rows = segments(content, "srt", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert [row["text"] for row in rows] == ["One", "Two"]
    assert rows[0]["start"] == 2.1


def test_json_segments_accept_normalized_array():
    content = json.dumps(
        [
            {"start": 0, "end": 2, "text": "First"},
            {"start": 2, "duration": 1.5, "text": "Second"},
        ]
    )
    rows = segments(content, "json", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert rows[1]["end"] == 3.5
    assert rows[1]["text"] == "Second"


def test_json_segments_accept_actual_json3_events():
    content = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 500,
                    "dDurationMs": 1250,
                    "segs": [{"utf8": "Hello "}, {"utf8": "world"}],
                }
            ]
        }
    )
    rows = segments(content, "json", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert rows == [
        {
            "start": 0.5,
            "end": 1.75,
            "text": "Hello world",
            "source_link": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s",
        }
    ]


@pytest.mark.parametrize(
    "content,format_name,message",
    [
        ("not json", "json", "timed text"),
        ("[]", "json", "1..5000"),
        ("WEBVTT\n\n00:broken --> 00:01.000\nText", "vtt", "timestamp"),
        ("WEBVTT\n\n00:02.000 --> 00:01.000\nText", "vtt", "order"),
        ("WEBVTT\n\n00:02.000 --> 00:03.000\n<i></i>", "vtt", "order"),
        (VTT, "txt", "format"),
    ],
)
def test_segments_reject_invalid_or_empty_timing(content, format_name, message):
    with pytest.raises(CaptureError, match=message):
        segments(content, format_name, URL)


def test_preview_is_stable_and_binds_original_content():
    first = preview(
        {
            "url": URL,
            "title": "Title",
            "format": "vtt",
            "content": VTT,
            "language": "en",
        }
    )
    repeated = preview(
        {
            "url": URL,
            "title": "Title",
            "format": "vtt",
            "content": VTT,
            "language": "en",
        }
    )
    changed = preview(
        {
            "url": URL,
            "title": "Title",
            "format": "vtt",
            "content": VTT.replace("Second", "Changed"),
            "language": "en",
        }
    )
    assert first == repeated
    assert changed["preview_id"] != first["preview_id"]
    assert first["text"] == "First & grounded caption\nSecond caption"


def test_preview_canonicalizes_every_segment_to_the_same_video_identity():
    result = preview({"url": "https://m.youtube.com/shorts/dQw4w9WgXcQ", "title": "Canonical", "format": "vtt", "content": VTT, "language": "en-US"})
    assert result["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert result["video_id"] == "dQw4w9WgXcQ"
    assert result["language"] == "en-US"
    assert all(row["source_link"].startswith(result["url"] + "&t=") for row in result["segments"])


def test_multiline_caption_text_is_preserved_as_one_timed_segment():
    content = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nFirst line\nSecond line\n"
    rows = segments(content, "vtt", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert len(rows) == 1
    assert rows[0]["text"] == "First line\nSecond line"
    assert rows[0]["end"] == 4.0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"url": URL, "title": "Title", "format": "vtt", "content": VTT},
        {
            "url": URL,
            "title": "Title",
            "format": "vtt",
            "content": VTT,
            "language": "en",
            "extra": True,
        },
        {"url": URL, "title": "", "format": "vtt", "content": VTT, "language": "en"},
        {
            "url": URL,
            "title": "Title",
            "format": "vtt",
            "content": VTT,
            "language": "bad language!",
        },
    ],
)
def test_preview_requires_exact_reviewed_fields(body):
    with pytest.raises(CaptureError):
        preview(body)


def test_reviewed_import_persists_three_canonical_records_and_provenance(service):
    job = service.import_preview(supplied())
    assert job["status"] == "completed"
    assert job["stage"] == "complete"
    assert job["video_id"] == "dQw4w9WgXcQ"
    assert [event["status"] for event in job["events"]] == [
        "pending",
        "running",
        "completed",
    ]
    source = service.store.get_item(job["source_id"])
    transcript = service.store.get_item(job["transcript_id"])
    original = service.store.get_item(job["original_source_id"])
    assert source["item_type"] == "bookmark"
    assert source["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert transcript["file_metadata"]["caption_provenance"] == "user_supplied"
    assert transcript["file_metadata"]["original_source_id"] == original["id"]
    assert transcript["file_metadata"]["segments"][1]["start"] == 62.0
    assert (
        "[00:01:02](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=62s) Second caption"
        in transcript["content"]
    )
    assert original["content"] == VTT


def test_reviewed_import_history_is_durable_across_service_reconstruction(service):
    created = service.import_preview(supplied("restart-video-history"))
    reopened = VideoIngests(service.store, service.home)
    restored = reopened.get(created["id"])
    assert restored["id"] == created["id"]
    assert restored["status"] == "completed"
    assert restored["events"] == created["events"]
    assert reopened.transcript(created["id"])["content"] == service.transcript(created["id"])["content"]


def test_progress_events_are_monotonic_and_retain_exact_stage_detail(service):
    body = {"request_id": "ordered-event-request", "url": URL, "language": "en", "transcript": True, "video": False, "audio": False}
    identity, _ = service._start(body["request_id"], {**body, "kind": "fetch"}, "Pending")
    service._event(identity, "metadata", "running", "Metadata started")
    service._event(identity, "transcript", "running", "Caption source selected")
    service._event(identity, "transcript", "failed", "Caption endpoint rejected the request")
    events = service.get(identity)["events"]
    assert [row["sequence"] for row in events] == [1, 2, 3, 4]
    assert [row["stage"] for row in events] == ["queued", "metadata", "transcript", "transcript"]
    assert events[-1]["detail"] == "Caption endpoint rejected the request"
    assert service.get(identity)["error"] == events[-1]["detail"]


def test_reviewed_import_retry_is_immutable_and_deduplicated(service):
    first = service.import_preview(supplied())
    second = service.import_preview(supplied())
    assert second == first
    assert (
        service.db.execute(
            "SELECT count(*) FROM capability_knowledge_video_jobs"
        ).fetchone()[0]
        == 1
    )
    assert (
        service.db.execute(
            "SELECT count(*) FROM items WHERE guid LIKE 'video:%'"
        ).fetchone()[0]
        == 3
    )


def test_second_review_of_same_video_updates_canonical_records_without_duplicate_identity(
    service,
):
    first = service.import_preview(supplied("same-video-first"))
    changed = supplied(
        "same-video-second",
        title="Corrected title",
        content=VTT.replace("Second caption", "Corrected second caption"),
    )
    changed["preview_id"] = preview(
        {key: changed[key] for key in ("url", "title", "format", "content", "language")}
    )["preview_id"]
    second = service.import_preview(changed)
    assert second["id"] != first["id"]
    assert second["source_id"] == first["source_id"]
    assert second["transcript_id"] == first["transcript_id"]
    assert second["original_source_id"] == first["original_source_id"]
    assert (
        service.db.execute(
            "SELECT count(*) FROM items WHERE guid LIKE 'video:%'"
        ).fetchone()[0]
        == 3
    )
    assert (
        service.store.get_item(second["transcript_id"])["title"]
        == "Corrected title · transcript"
    )
    assert "Corrected second caption" in service.transcript(second["id"])["content"]


def test_new_video_keeps_distinct_canonical_source_identity(service):
    first = service.import_preview(supplied("distinct-video-first"))
    other = supplied("distinct-video-second", url="https://youtu.be/aqz-KE-bpKQ")
    other["preview_id"] = preview(
        {key: other[key] for key in ("url", "title", "format", "content", "language")}
    )["preview_id"]
    second = service.import_preview(other)
    assert second["video_id"] == "aqz-KE-bpKQ"
    assert second["source_id"] != first["source_id"]
    assert second["transcript_id"] != first["transcript_id"]
    assert (
        service.db.execute(
            "SELECT count(*) FROM items WHERE guid LIKE 'video:%'"
        ).fetchone()[0]
        == 6
    )


def test_request_id_cannot_be_reused_for_changed_input(service):
    service.import_preview(supplied())
    changed = supplied(title="Different title")
    changed["preview_id"] = preview(
        {key: changed[key] for key in ("url", "title", "format", "content", "language")}
    )["preview_id"]
    with pytest.raises(CaptureError, match="different video ingest"):
        service.import_preview(changed)


def test_import_rejects_caption_changed_after_review(service):
    body = supplied()
    body["content"] = body["content"].replace("Second", "Changed")
    with pytest.raises(CaptureError, match="changed after review"):
        service.import_preview(body)
    assert service.list()["total"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.pop("request_id"),
        lambda body: body.update({"unexpected": "selector"}),
        lambda body: body.update({"request_id": "short"}),
        lambda body: body.update({"preview_id": "wrong"}),
        lambda body: body.update({"language": "space separated"}),
    ],
)
def test_import_rejects_incomplete_changed_or_unreviewed_payload(service, mutation):
    body = supplied("strict-video-request")
    mutation(body)
    with pytest.raises(CaptureError):
        service.import_preview(body)
    assert (
        service.db.execute(
            "SELECT count(*) FROM capability_knowledge_video_jobs"
        ).fetchone()[0]
        == 0
    )


def test_home_drift_refuses_import_without_writing_other_home(
    service, monkeypatch, tmp_path
):
    other = tmp_path / "other"
    monkeypatch.setenv("GIDEON_HOME", str(other))
    with pytest.raises(CaptureError, match="Runtime home changed"):
        service.import_preview(supplied())
    assert not other.exists()
    assert service.list()["total"] == 0


def test_home_drift_refuses_artifact_write_without_creating_other_home(service, monkeypatch, tmp_path):
    body = {"request_id": "artifact-home-drift", "url": URL, "language": "en", "transcript": False, "video": True, "audio": False}
    identity, _ = service._start(body["request_id"], {**body, "kind": "fetch"}, "Pending")
    other = tmp_path / "different-allocation"
    monkeypatch.setenv("GIDEON_HOME", str(other))
    with pytest.raises(CaptureError, match="Runtime home changed"):
        service._save_artifact(identity, "video", b"must not land", "mp4")
    assert not other.exists()
    assert service.get(identity)["artifacts"] == []


def test_list_pages_jobs_and_reports_caption_dependency(service):
    for index in range(3):
        service.import_preview(supplied(f"video-request-{index:04d}"))
    first = service.list(limit=2)
    second = service.list(limit=2, offset=2)
    assert first["total"] == 3
    assert first["next_offset"] == 2
    assert len(first["items"]) == 2
    assert second["next_offset"] is None
    assert len(second["items"]) == 1
    assert set(first["availability"]) == {"caption_retrieval", "reason"}


@pytest.mark.parametrize(
    "limit,offset", [(0, 0), (101, 0), (20, -1), (True, 0), (20, 1000001)]
)
def test_list_rejects_invalid_paging(service, limit, offset):
    with pytest.raises(CaptureError, match="limit"):
        service.list(limit, offset)


def test_transcript_returns_canonical_markdown_and_link(service):
    job = service.import_preview(supplied())
    result = service.transcript(job["id"])
    assert result["transcript_id"] == job["transcript_id"]
    assert result["source_link"] == "#/knowledge/item/" + job["transcript_id"]
    assert result["content"].startswith("# A useful public talk")


def test_missing_job_and_transcript_are_explicit(service):
    with pytest.raises(CaptureError, match="not found"):
        service.get("missing")
    body = {
        "request_id": "video-fetch-0001",
        "url": URL,
        "language": "en",
        "transcript": True,
        "video": False,
        "audio": False,
    }
    identity, _ = service._start(
        body["request_id"], {**body, "kind": "fetch"}, "Pending"
    )
    with pytest.raises(CaptureError, match="No transcript"):
        service.transcript(identity)


@pytest.mark.parametrize("value,expected", [(0, "00:00:00"), (59.9, "00:00:59"), (60, "00:01:00"), (3661, "01:01:01"), (21600, "06:00:00")])
def test_markdown_clock_is_stable_and_bounded(value, expected):
    assert VideoIngests._clock(value) == expected


def test_restart_marks_unfinished_jobs_interrupted(service):
    body = {
        "request_id": "video-fetch-0002",
        "url": URL,
        "language": "en",
        "transcript": True,
        "video": False,
        "audio": False,
    }
    identity, _ = service._start(
        body["request_id"], {**body, "kind": "fetch"}, "Pending"
    )
    service._event(identity, "metadata", "running", "Reading")
    restarted = VideoIngests(service.store, service.home)
    job = restarted.get(identity)
    assert job["status"] == "interrupted"
    assert "Runtime stopped" in job["error"]
    assert [event["status"] for event in job["events"]] == ["pending", "running"]


def test_cancel_sets_real_shared_event_and_persists_request(service):
    body = {
        "request_id": "video-fetch-0003",
        "url": URL,
        "language": "en",
        "transcript": True,
        "video": False,
        "audio": False,
    }
    identity, _ = service._start(
        body["request_id"], {**body, "kind": "fetch"}, "Pending"
    )
    cancellation = Event()
    service.cancellations[identity] = cancellation
    service._event(identity, "metadata", "running", "Reading")
    job = service.cancel(identity)
    assert cancellation.is_set()
    assert job["status"] == "cancelling"
    assert job["events"][-1]["detail"] == "Cancellation requested"
    with pytest.raises(CaptureError, match="not running"):
        service.cancel(service.import_preview(supplied("finished-video-request"))["id"])


def test_artifact_bytes_are_hash_named_verified_and_durable(service):
    body = {
        "request_id": "video-fetch-0004",
        "url": URL,
        "language": "en",
        "transcript": False,
        "video": True,
        "audio": False,
    }
    identity, _ = service._start(
        body["request_id"], {**body, "kind": "fetch"}, "Pending"
    )
    service._save_artifact(identity, "video", b"real artifact bytes", "mp4")
    artifact = service.get(identity)["artifacts"][0]
    path = Path(artifact["path"])
    assert path.read_bytes() == b"real artifact bytes"
    assert path.name == "video-" + artifact["sha256"] + ".mp4"
    assert path.is_relative_to(service.home)


def test_multiple_landed_artifacts_remain_independently_auditable(service):
    body = {
        "request_id": "video-fetch-artifacts",
        "url": URL,
        "language": "en",
        "transcript": False,
        "video": True,
        "audio": True,
    }
    identity, _ = service._start(
        body["request_id"], {**body, "kind": "fetch"}, "Pending"
    )
    service._save_artifact(identity, "audio", b"audio bytes", "m4a")
    service._save_artifact(identity, "video", b"video bytes", "mp4")
    job = service.get(identity)
    assert [artifact["kind"] for artifact in job["artifacts"]] == ["audio", "video"]
    assert len({artifact["sha256"] for artifact in job["artifacts"]}) == 2
    for artifact in job["artifacts"]:
        assert Path(artifact["path"]).stat().st_size == artifact["bytes"]


def test_late_failure_keeps_previously_landed_transcript_and_artifact(service):
    completed = service.import_preview(supplied("landed-transcript"))
    body = {
        "request_id": "late-failure-request",
        "url": URL,
        "language": "en",
        "transcript": False,
        "video": True,
        "audio": False,
    }
    identity, _ = service._start(
        body["request_id"], {**body, "kind": "fetch"}, "Pending"
    )
    service.db.execute(
        "UPDATE capability_knowledge_video_jobs SET source_id=?,transcript_id=?,original_source_id=? WHERE id=?",
        (
            completed["source_id"],
            completed["transcript_id"],
            completed["original_source_id"],
            identity,
        ),
    )
    service.db.commit()
    service._save_artifact(identity, "video", b"landed before later failure", "mp4")
    service._event(identity, "audio", "failed", "Later requested audio was unavailable")
    failed = service.get(identity)
    assert failed["status"] == "failed"
    assert failed["artifacts"][0]["kind"] == "video"
    assert "First & grounded caption" in service.transcript(identity)["content"]
    assert service.store.get_item(failed["source_id"])


def test_empty_artifact_is_never_recorded(service):
    body = {
        "request_id": "video-fetch-0005",
        "url": URL,
        "language": "en",
        "transcript": False,
        "video": True,
        "audio": False,
    }
    identity, _ = service._start(
        body["request_id"], {**body, "kind": "fetch"}, "Pending"
    )
    with pytest.raises(CaptureError, match="empty"):
        service._save_artifact(identity, "video", b"", "mp4")
    assert service.get(identity)["artifacts"] == []


@pytest.mark.asyncio
async def test_fetch_job_uses_real_adapter_and_reports_unavailable_or_external_outcome(
    service,
):
    body = {
        "request_id": "video-fetch-0006",
        "url": URL,
        "language": "en",
        "transcript": True,
        "video": False,
        "audio": False,
    }
    job = service.start_fetch(body, "dashboard:ui")
    assert job["status"] == "pending"
    await service.tasks[job["id"]]
    finished = service.get(job["id"])
    assert finished["status"] in {"completed", "failed"}
    if finished["status"] == "failed":
        assert finished["error"]
    else:
        assert finished["transcript_id"]
        assert service.transcript(job["id"])["content"]


@pytest.mark.parametrize(
    "body,message",
    [
        ({}, "requires"),
        (
            {
                "request_id": "video-fetch-0007",
                "url": URL,
                "language": "en",
                "transcript": False,
                "video": False,
                "audio": False,
            },
            "Choose",
        ),
        (
            {
                "request_id": "video-fetch-0008",
                "url": URL,
                "language": "",
                "transcript": True,
                "video": False,
                "audio": False,
            },
            "language",
        ),
        (
            {
                "request_id": "video-fetch-0009",
                "url": URL,
                "language": "en",
                "transcript": 1,
                "video": False,
                "audio": False,
            },
            "Choose",
        ),
    ],
)
def test_fetch_requires_exact_valid_selection(service, body, message):
    with pytest.raises(CaptureError, match=message):
        service.start_fetch(body, "dashboard:ui")


def test_video_reader_rejects_nonfixed_hosts_and_cancelled_work():
    reader = VideoReader("dashboard:ui")
    with pytest.raises(CaptureError, match="fixed public video"):
        reader.validate("https://example.com/video")
    cancelled = Event()
    cancelled.set()
    with pytest.raises(VideoCancelled):
        VideoReader("dashboard:ui", cancelled).validate(URL)


@pytest.mark.asyncio
async def test_fetch_calls_extra_validator_before_any_network_access():
    seen = []

    def reject(url):
        seen.append(url)
        raise CaptureError("connector rejected target")

    policy = EgressPolicy(
        name="validator-test", allow_hosts=("youtube.com",), max_bytes=10
    )
    with pytest.raises(CaptureError, match="connector rejected"):
        await fetch(
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            policy=policy,
            validate_url=reject,
        )
    assert seen == ["https://youtube.com/watch?v=dQw4w9WgXcQ"]


@pytest.mark.asyncio
async def test_http_preview_import_list_detail_transcript_and_guards(
    tmp_path, monkeypatch
):
    home = tmp_path / "http-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    path = home / "workspace" / "knowledge" / "knowledge.db"
    path.parent.mkdir(parents=True)
    store = KnowledgeStore(str(path))
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = store
    app = web.Application()
    app["state"] = state
    register(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        raw = {
            key: supplied()[key]
            for key in ("url", "title", "format", "content", "language")
        }
        response = await client.post(
            "/api/capabilities/knowledge/videos/preview",
            json=raw,
            headers={"X-Session-Key": "dashboard:ui"},
        )
        assert response.status == 200
        reviewed = await response.json()
        response = await client.post(
            "/api/capabilities/knowledge/videos/import",
            json={
                "request_id": "http-video-request",
                **raw,
                "preview_id": reviewed["preview_id"],
            },
            headers={"X-Session-Key": "dashboard:ui"},
        )
        assert response.status == 200
        job = await response.json()
        listing = await (
            await client.get(
                "/api/capabilities/knowledge/videos?limit=1&offset=0",
                headers={"X-Session-Key": "dashboard:ui"},
            )
        ).json()
        assert listing["items"][0]["id"] == job["id"]
        detail = await (
            await client.get(
                "/api/capabilities/knowledge/videos/" + job["id"],
                headers={"X-Session-Key": "dashboard:ui"},
            )
        ).json()
        assert detail["status"] == "completed"
        transcript = await (
            await client.get(
                f"/api/capabilities/knowledge/videos/{job['id']}/transcript",
                headers={"X-Session-Key": "dashboard:ui"},
            )
        ).json()
        assert "First & grounded caption" in transcript["content"]
        assert (
            await client.get(
                "/api/capabilities/knowledge/videos?unknown=1",
                headers={"X-Session-Key": "dashboard:ui"},
            )
        ).status == 400
        assert (
            await client.get(
                "/api/capabilities/knowledge/videos/missing",
                headers={"X-Session-Key": "dashboard:ui"},
            )
        ).status == 404
        assert (
            await client.post(
                "/api/capabilities/knowledge/videos/fetch",
                json={
                    "request_id": "http-fetch-request",
                    "url": URL,
                    "language": "en",
                    "transcript": True,
                    "video": False,
                    "audio": False,
                },
            )
        ).status == 403
    finally:
        await client.close()
        store.close()

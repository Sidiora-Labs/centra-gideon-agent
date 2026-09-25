"""Timestamped supplied and retrieved captions with explicit source provenance."""

import html
import json
import math
import re
from urllib.parse import parse_qs, urlparse

from .capture import CaptureError, text_field
from .reviews import digest


def video_url(value):
    text_field(value, "url", 2048)
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError()
        host = parsed.hostname
        if host == "youtu.be":
            identity = parsed.path.strip("/")
        elif host in (
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "www.youtube-nocookie.com",
        ):
            if parsed.path == "/watch":
                ids = parse_qs(parsed.query).get("v", [])
                identity = ids[0] if len(ids) == 1 else ""
            else:
                match = re.fullmatch(
                    r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", parsed.path
                )
                identity = match[1] if match else ""
        else:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity):
            raise ValueError()
    except ValueError:
        raise CaptureError("A single HTTPS YouTube video URL is required") from None
    return identity, "https://www.youtube.com/watch?v=" + identity


def seconds(value):
    if isinstance(value, bool):
        raise CaptureError("Caption timestamps must be finite nonnegative numbers")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise CaptureError("Caption timestamp is invalid") from None
    if not math.isfinite(result) or not 0 <= result <= 86400:
        raise CaptureError("Caption timestamp is outside0..86400seconds")
    return result


def stamp(value):
    parts = value.replace(",", ".").split(":")
    if len(parts) not in (2, 3):
        raise CaptureError("Caption timestamp must be MM:SS.mmm or HH:MM:SS.mmm")
    values = [seconds(part) for part in parts]
    if any(part >= 60 for part in values[-2:]):
        raise CaptureError("Caption timestamp minute/second field is invalid")
    return seconds(sum(part * 60**index for index, part in enumerate(reversed(values))))


def segments(content, format, url):
    text_field(content, "content", 1048576)
    rows = []
    if format == "json":
        try:
            decoded = json.loads(content)
            if isinstance(decoded, dict) and "events" in decoded:
                decoded = [
                    {
                        "start": event.get("tStartMs", 0) / 1000,
                        "duration": event.get("dDurationMs", 0) / 1000,
                        "text": "".join(
                            part.get("utf8", "") for part in event.get("segs", [])
                        ),
                    }
                    for event in decoded["events"]
                    if event.get("segs")
                ]
            if not isinstance(decoded, list):
                raise ValueError()
            for row in decoded:
                start = seconds(row["start"])
                end = (
                    seconds(row["end"])
                    if "end" in row
                    else seconds(start + seconds(row.get("duration", 0)))
                )
                rows.append((start, end, row["text"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CaptureError(
                "Transcript JSON must contain timed text segments"
            ) from None
    elif format in ("vtt", "srt"):
        lines = content.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if "-->" not in line:
                index += 1
                continue
            timing = re.fullmatch(r"(\S+)\s+-->\s+(\S+)(?:\s+.*)?", line)
            if not timing:
                raise CaptureError("Malformed caption timing line")
            start, end = stamp(timing[1]), stamp(timing[2])
            text = []
            index += 1
            while index < len(lines) and lines[index].strip():
                text.append(lines[index])
                index += 1
            rows.append((start, end, "\n".join(text)))
    else:
        raise CaptureError("Transcript format must be vtt, srt or json")
    if not rows or len(rows) > 5000:
        raise CaptureError("Transcript requires1..5000 timed segments")
    result = []
    previous = -1.0
    for start, end, value in rows:
        text_field(value, "segment text", 10000)
        clean = html.unescape(re.sub(r"<[^>]*>", "", value)).strip()
        if not clean or end < start or start < previous:
            raise CaptureError("Caption order, interval or text is invalid")
        result.append(
            {
                "start": start,
                "end": end,
                "text": clean,
                "source_link": url + "&t=" + str(int(start)) + "s",
            }
        )
        previous = start
    return result


def preview(body):
    if not isinstance(body, dict) or set(body) != {
        "url",
        "title",
        "format",
        "content",
        "language",
    }:
        raise CaptureError(
            "Transcript preview requires url, title, format, content and language"
        )
    identity, url = video_url(body["url"])
    text_field(body["title"], "title", 300)
    text_field(body["language"], "language", 30)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", body["language"]):
        raise CaptureError("Language tag is invalid")
    cues = segments(body["content"], body["format"], url)
    result = {
        "video_id": identity,
        "url": url,
        "title": body["title"],
        "format": body["format"],
        "language": body["language"],
        "segments": cues,
        "text": "\n".join(row["text"] for row in cues),
    }
    return {
        **result,
        "preview_id": digest({**result, "original_content": body["content"]}),
    }

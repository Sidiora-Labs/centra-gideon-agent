"""Real video metadata and artifact reads through the canonical pinned egress guard."""

import asyncio
import importlib.util
import io
from dataclasses import replace
from threading import Event
from urllib.parse import urlparse
from gideon.security.net import STRICT, egress_policy_for, evaluate, fetch
from gideon.security.net.policy import egress_policy_for_profile
from gideon.security.guardrails.policy import profile_for_session
from .capture import CaptureError
from .transcript_format import video_url


class VideoCancelled(CaptureError):
    pass


def available():
    return importlib.util.find_spec("yt_dlp") is not None


class VideoReader:
    def __init__(self, session_key, cancelled=None):
        self.cancelled = cancelled or Event()
        self.policy = egress_policy_for_profile(
            egress_policy_for(STRICT), profile_for_session(session_key).egress_tier
        )
        self.bytes = 0

    def validate(self, url):
        if self.cancelled.is_set():
            raise VideoCancelled("Video acquisition cancelled", 409)
        parsed = urlparse(url)
        host = parsed.hostname or ""
        hosts = (
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "www.youtube-nocookie.com",
            "youtubei.googleapis.com",
        )
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not (
                host in hosts
                or host.endswith(".googlevideo.com")
                or host.endswith(".ytimg.com")
            )
        ):
            raise CaptureError(
                "Video acquisition URL is outside fixed public video endpoints", 400
            )
        if self.policy is None:
            raise CaptureError("Session egress policy disables video acquisition", 403)
        decision = evaluate(url, self.policy)
        if not decision.allow:
            raise CaptureError(
                "Video acquisition blocked by session egress policy: "
                + decision.reason,
                403,
            )

    async def read(
        self, url, *, method="GET", headers=None, data=None, max_bytes=8388608
    ):
        self.validate(url)
        policy = replace(
            self.policy,
            allow_private=False,
            loopback_only=False,
            allow_hosts=(),
            allow_only=False,
            pin_resolved_ip=True,
            max_bytes=min(self.policy.max_bytes, max_bytes),
            timeout_s=min(self.policy.timeout_s, 20),
            max_redirects=min(self.policy.max_redirects, 5),
        )
        result = await fetch(
            url,
            policy=policy,
            method=method,
            headers=headers,
            data=data,
            validate_url=self.validate,
        )
        self.bytes += len(result.body)
        if self.bytes > 67108864 or result.truncated:
            raise CaptureError("Video acquisition exceeded its byte limit", 413)
        if self.cancelled.is_set():
            raise VideoCancelled("Video acquisition cancelled", 409)
        return result

    def metadata(self, url):
        video_url(url)
        if not available():
            raise CaptureError(
                "Video caption reader is unavailable; install the optional yt-dlp dependency or supply an original transcript",
                503,
            )
        import yt_dlp
        from yt_dlp.networking.common import Response
        from yt_dlp.networking.exceptions import HTTPError

        reader = self

        class GuardedReader(yt_dlp.YoutubeDL):
            def urlopen(self, request):
                url = request if isinstance(request, str) else request.url
                response = asyncio.run(
                    reader.read(
                        url,
                        method=getattr(request, "method", "GET"),
                        headers=dict(getattr(request, "headers", {})),
                        data=getattr(request, "data", None),
                    )
                )
                result = Response(
                    io.BytesIO(response.body),
                    response.url,
                    response.headers,
                    status=response.status,
                )
                if response.status >= 400:
                    raise HTTPError(result)
                return result

        with GuardedReader(
            {
                "quiet": True,
                "no_warnings": True,
                "cachedir": False,
                "noplaylist": True,
                "skip_download": True,
                "ignore_no_formats_error": True,
                "extractor_args": {"youtube": {"player_client": ["web"]}},
            }
        ) as downloader:
            return downloader.extract_info(url, download=False)

    async def captions(self, metadata, language):
        for field, provenance in (
            ("subtitles", "manual"),
            ("automatic_captions", "automatic"),
        ):
            tracks = metadata.get(field) or {}
            key = (
                language
                if language in tracks
                else next(
                    (
                        name
                        for name in tracks
                        if name.split("-")[0] == language.split("-")[0]
                    ),
                    None,
                )
            )
            if key is None:
                continue
            options = tracks[key]
            for extension, format in (("vtt", "vtt"), ("json3", "json")):
                track = next(
                    (
                        row
                        for row in options
                        if row.get("ext") == extension and row.get("url")
                    ),
                    None,
                )
                if track:
                    result = await self.read(track["url"], max_bytes=1048576)
                    if result.status != 200:
                        raise CaptureError(
                            "Caption source returned HTTP " + str(result.status), 502
                        )
                    return result.text, format, key, provenance
        raise CaptureError("No matching caption track is available for this video", 404)

    async def media(self, metadata, kind):
        candidates = [
            row
            for row in metadata.get("formats", [])
            if row.get("url")
            and row.get("protocol") in ("https", "http")
            and not row.get("has_drm")
        ]
        if kind == "audio":
            candidates = [
                row
                for row in candidates
                if row.get("vcodec") == "none" and row.get("acodec") != "none"
            ]
        else:
            candidates = [
                row
                for row in candidates
                if row.get("vcodec") != "none" and row.get("acodec") != "none"
            ]
        candidates.sort(
            key=lambda row: (
                row.get("filesize") or row.get("filesize_approx") or 0,
                row.get("height") or 0,
            )
        )
        for row in candidates:
            size = row.get("filesize") or row.get("filesize_approx") or 0
            if size > 33554432:
                continue
            result = await self.read(row["url"], max_bytes=33554432)
            if result.status == 200:
                return result.body, row.get("ext") or (
                    "m4a" if kind == "audio" else "mp4"
                )
        raise CaptureError(
            "No supported bounded single-file " + kind + " stream is available", 404
        )

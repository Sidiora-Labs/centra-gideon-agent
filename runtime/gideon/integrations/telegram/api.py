"""Bounded Telegram Bot API requests with credential-safe errors."""

from __future__ import annotations
import asyncio
import json
import logging
import re
import os
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit
import httpx
from gideon.security.guardrails.writes import live_writes_disabled

MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_UPLOAD = 50 * 1024 * 1024


class TelegramError(Exception):
    def __init__(self, code: int, description: str, *, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(re.sub(r"\d{5,}:[A-Za-z0-9_-]+", "[redacted]", description))


class TelegramLogFilter(logging.Filter):
    def filter(self, record):
        rendered = record.getMessage()
        redacted = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot[redacted]", rendered)
        if redacted != rendered:
            record.msg, record.args = redacted, ()
        return True


class TelegramAPI:
    def __init__(self, token: str, *, fallback_ips=()):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise TelegramError(401, "Invalid Telegram bot token")
        for name in (
            "httpx",
            "gideon.integrations.telegram.network",
            "httpcore.http11",
            "httpcore.http2",
            "httpcore.connection",
        ):
            logger = logging.getLogger(name)
            if not any(isinstance(item, TelegramLogFilter) for item in logger.filters):
                logger.addFilter(TelegramLogFilter())
        self._token = token
        self.base_url = os.environ.get(
            "GIDEON_TELEGRAM_API_BASE", "https://api.telegram.org"
        ).rstrip("/")
        base = urlsplit(self.base_url)
        if (
            base.scheme not in ("http", "https")
            or not base.hostname
            or base.username
            or base.password
            or base.query
            or base.fragment
        ):
            raise TelegramError(400, "Invalid operator Telegram API endpoint")
        local = self.base_url != "https://api.telegram.org"
        self.max_download = 2 * 1024**3 if local else MAX_DOWNLOAD
        self.max_upload = 2 * 1024**3 if local else MAX_UPLOAD
        from .network import TelegramFallbackTransport

        self.client = httpx.AsyncClient(
            transport=TelegramFallbackTransport(fallback_ips) if fallback_ips else None,
            timeout=httpx.Timeout(65, connect=10),
            follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    async def call(self, method: str, *, files=None, **payload):
        read_only = method in {
            "getMe",
            "getUpdates",
            "getFile",
            "getChat",
            "getWebhookInfo",
        }
        if not read_only and live_writes_disabled():
            raise TelegramError(403, "GIDEON_DISABLE_LIVE_WRITES is enabled")
        payload = {k: v for k, v in payload.items() if v is not None}
        for attempt in range(4):
            try:
                if files:
                    for item in files.values():
                        if hasattr(item[1], "seek"):
                            item[1].seek(0)
                if files:
                    data = {
                        k: (
                            json.dumps(v)
                            if isinstance(v, (dict, list, bool))
                            else str(v)
                        )
                        for k, v in payload.items()
                    }
                    response = await self.client.post(
                        f"{self.base_url}/bot{self._token}/{method}",
                        data=data,
                        files=files,
                    )
                else:
                    response = await self.client.post(
                        f"{self.base_url}/bot{self._token}/{method}",
                        json=payload,
                    )
                body = response.json()
            except (httpx.HTTPError, ValueError):
                # Retrying a send after an ambiguous timeout can send it twice.
                if read_only and attempt < 3:
                    await asyncio.sleep(2**attempt)
                    continue
                raise TelegramError(
                    0, "Telegram request failed; delivery may be uncertain"
                ) from None
            if body.get("ok"):
                return body.get("result")
            code = int(body.get("error_code", response.status_code))
            if code == 429 and attempt < 3:
                delay = max(1, float(body.get("parameters", {}).get("retry_after", 1)))
                if delay > 60:
                    raise TelegramError(
                        429,
                        f"Telegram rate limit; retry after {int(delay)} seconds",
                        retry_after=delay,
                    )
                await asyncio.sleep(delay)
                continue
            if code >= 500 and read_only and attempt < 3:
                await asyncio.sleep(2**attempt)
                continue
            description = str(
                body.get("description", "Telegram request failed")
            ).replace(self._token, "[redacted]")
            raise TelegramError(code, description)
        raise TelegramError(0, "Telegram retry limit reached")

    async def download(self, file_id: str, destination: Path):
        meta = await self.call("getFile", file_id=file_id)
        if int(meta.get("file_size", 0)) > self.max_download:
            raise TelegramError(413, "Telegram attachment exceeds the download limit")
        remote = str(meta.get("file_path", ""))
        if remote.startswith("/"):
            root = os.environ.get("GIDEON_TELEGRAM_FILE_ROOT", "")
            source = Path(remote).resolve()
            if (
                self.base_url == "https://api.telegram.org"
                or not root
                or not source.is_relative_to(Path(root).resolve())
                or not source.is_file()
            ):
                raise TelegramError(
                    403, "Local Bot API file is outside the operator file root"
                )
            if source.stat().st_size > self.max_download:
                raise TelegramError(
                    413, "Telegram attachment exceeds the download limit"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as src, destination.open("xb") as dst:
                destination.chmod(0o600)
                await asyncio.to_thread(shutil.copyfileobj, src, dst)
            return destination
        if not remote or ".." in remote.split("/"):
            raise TelegramError(400, "Invalid Telegram attachment path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            async with self.client.stream(
                "GET",
                f'{self.base_url}/file/bot{self._token}/{quote(remote, safe="/")}',
            ) as response:
                if response.status_code != 200:
                    raise TelegramError(
                        response.status_code, "Telegram attachment download failed"
                    )
                total = 0
                with destination.open("xb") as output:
                    created = True
                    destination.chmod(0o600)
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self.max_download:
                            raise TelegramError(
                                413, "Telegram attachment exceeds the download limit"
                            )
                        output.write(chunk)
        except BaseException as exc:
            if created:
                destination.unlink(missing_ok=True)
            if isinstance(exc, httpx.HTTPError):
                raise TelegramError(0, "Telegram attachment download failed") from None
            raise
        return destination

    async def upload(self, kind: str, channel: str, path: str, **options):
        from gideon.engine.hooks import validate_file_path

        allowed = validate_file_path(path)
        if not allowed:
            raise TelegramError(403, "Attachment path is not allowed")
        target = Path(allowed)
        if target.stat().st_size > self.max_upload:
            raise TelegramError(413, "Telegram upload exceeds the upload limit")
        methods = {
            "photo": "sendPhoto",
            "document": "sendDocument",
            "voice": "sendVoice",
            "audio": "sendAudio",
            "video": "sendVideo",
            "animation": "sendAnimation",
        }
        with target.open("rb") as stream:
            # The request loop rewinds files only for explicitly rejected rate limits.
            return await self.call(
                methods[kind],
                files={kind: (target.name, stream)},
                chat_id=channel,
                **options,
            )

    async def upload_album(self, channel, paths, **options):
        from contextlib import ExitStack
        from gideon.engine.hooks import validate_file_path

        if not 2 <= len(paths) <= 10:
            raise TelegramError(400, "An album needs two to ten files")
        with ExitStack() as stack:
            files, media = {}, []
            for index, raw in enumerate(paths):
                allowed = validate_file_path(raw)
                if not allowed:
                    raise TelegramError(403, "Attachment path is not allowed")
                path = Path(allowed)
                if path.stat().st_size > self.max_upload:
                    raise TelegramError(413, "Album file exceeds the upload limit")
                kind = (
                    "photo"
                    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                    else (
                        "video"
                        if path.suffix.lower() in (".mp4", ".mov")
                        else "document"
                    )
                )
                name = f"file{index}"
                files[name] = (path.name, stack.enter_context(path.open("rb")))
                media.append({"type": kind, "media": f"attach://{name}"})
            if any(v["type"] == "document" for v in media) and any(
                v["type"] != "document" for v in media
            ):
                raise TelegramError(
                    400, "Send documents in a separate album from photos and videos"
                )
            return await self.call(
                "sendMediaGroup", files=files, chat_id=channel, media=media, **options
            )

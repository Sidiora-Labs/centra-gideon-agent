"""Slack events file-transcription plumbing + client download_file contract
(moved from core tests/test_transcribe.py; core transcription itself is tested
there — this is the slack_desk_runtime.events/client integration layer)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# events.py: _transcribe_files
# ---------------------------------------------------------------------------


class TestTranscribeFiles:
    @pytest.mark.asyncio
    async def test_transcribe_audio_files(self):
        from slack_desk_runtime.events import _transcribe_files

        mock_orch = MagicMock()
        mock_orch.slack_desk = AsyncMock()
        mock_orch.slack_desk.download_file = AsyncMock()

        files = [
            {
                "mimetype": "audio/webm",
                "url_private_download": "https://files.slack.com/a.webm",
                "filetype": "webm",
                "name": "voice.webm",
            },
        ]

        with patch(
            "gideon.sdk.channel.transcribe_audio", new_callable=AsyncMock, return_value="Hello"
        ):
            result = await _transcribe_files(mock_orch, files)
        assert result == ["Hello"]

    @pytest.mark.asyncio
    async def test_skips_non_audio(self):
        from slack_desk_runtime.events import _transcribe_files

        mock_orch = MagicMock()
        mock_orch.slack_desk = AsyncMock()

        files = [
            {"mimetype": "image/png", "url_private": "https://x.com/img.png", "name": "pic.png"}
        ]

        result = await _transcribe_files(mock_orch, files)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_no_url(self):
        from slack_desk_runtime.events import _transcribe_files

        mock_orch = MagicMock()
        mock_orch.slack_desk = AsyncMock()

        files = [{"mimetype": "audio/webm", "name": "voice.webm"}]

        result = await _transcribe_files(mock_orch, files)
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_transcription_failure(self):
        from slack_desk_runtime.events import _transcribe_files

        mock_orch = MagicMock()
        mock_orch.slack_desk = AsyncMock()
        mock_orch.slack_desk.download_file = AsyncMock()

        files = [
            {
                "mimetype": "audio/webm",
                "url_private_download": "https://x.com/a.webm",
                "filetype": "webm",
                "name": "v.webm",
            },
        ]

        with patch(
            "gideon.transcribe.transcribe_audio", new_callable=AsyncMock, return_value=None
        ):
            result = await _transcribe_files(mock_orch, files)
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        from slack_desk_runtime.events import _transcribe_files

        mock_orch = MagicMock()
        mock_orch.slack_desk = AsyncMock()
        mock_orch.slack_desk.download_file = AsyncMock(side_effect=Exception("download failed"))

        files = [
            {
                "mimetype": "audio/webm",
                "url_private_download": "https://x.com/a.webm",
                "filetype": "webm",
                "name": "v.webm",
            },
        ]

        result = await _transcribe_files(mock_orch, files)
        assert result == []


# ---------------------------------------------------------------------------
# client.py: download_file
# ---------------------------------------------------------------------------


class TestSlackDeskClientDownloadFile:
    @pytest.mark.asyncio
    async def test_base_class_raises(self):
        from slack_desk_runtime.client import SlackDeskClientOps

        class MinimalClient(SlackDeskClientOps):
            async def post_message(self, *a, **kw): ...
            async def post_blocks(self, *a, **kw): ...
            async def update_message(self, *a, **kw): ...
            async def delete_message(self, *a, **kw): ...
            async def add_reaction(self, *a, **kw): ...
            async def remove_reaction(self, *a, **kw): ...
            async def open_dm(self, *a, **kw): ...
            async def post_ephemeral(self, *a, **kw): ...
            async def views_publish(self, *a, **kw): ...
            async def views_open(self, *a, **kw): ...
            async def views_update(self, *a, **kw): ...
            async def upload_file(self, *a, **kw): ...

        client = MinimalClient()
        with pytest.raises(NotImplementedError):
            await client.download_file("https://example.com/f", "/tmp/out")

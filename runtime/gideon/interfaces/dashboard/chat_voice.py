"""Voice synthesis endpoint — streaming Piper TTS for the dashboard.

The active voice + speaking speed resolve from the unified model store
(``active_models.json`` ``tts`` selection + ``use_case_settings/tts.json``) via
``tts.registry.active_voice_params``.
"""

import base64
import contextlib
import logging
import os
import tempfile

from aiohttp import web

from gideon.core.config import AppConfig
from gideon.http_errors import json_error
from gideon.integrations.tts.registry import active_voice_params
from gideon.integrations.voice.duplex import clean_for_speech
from gideon.integrations.voice.profiles import VoiceProfileError, append_history
from gideon.integrations.voice_reply import stitch_wavs, streaming_voice_reply
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)


def _record_generation(params: dict, wav_path: str, text: str) -> None:
    """Append this generation to the resolved profile's bounded history (§1.2).

    Only profile-routed syntheses are recorded — the flat built-in path has no entity
    to remember them against. The stored text is a HASH, not the transcript: history
    exists to let the user re-lock a voice they liked, not to accumulate a second copy
    of everything the assistant said.
    """
    profile_id = str(params.get("profile_id") or "")
    if not profile_id:
        return
    import hashlib
    from pathlib import Path

    try:
        append_history(
            profile_id,
            Path(wav_path),
            seed=int(params.get("seed") or 0),
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        )
    except Exception:
        logger.debug("voice history append failed for %s", profile_id, exc_info=True)


async def api_voice_synthesize(request: web.Request) -> web.Response:
    """POST /api/voice/synthesize — sentence-chunked Piper TTS.

    Synthesizes each sentence sequentially, broadcasts ``voice_chunk``
    WS events with base64 WAV data for immediate playback, then stitches
    all chunks into a single WAV and broadcasts ``voice_complete``.
    """

    state: ConsoleState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    text = body.get("text", "")
    if not isinstance(text, str):
        return web.json_response({"error": "text must be a string"}, status=400)
    text = text.strip()
    session_name = body.get("session", "")
    if not isinstance(session_name, str):
        session_name = ""
    if not text:
        return web.json_response({"error": "text required"}, status=400)

    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)

    if AppConfig.load().voice.clean_for_speech_enabled:
        spoken = clean_for_speech(text)
        if spoken:
            text = spoken
        else:
            logger.debug("clean_for_speech emptied the text; speaking it unchanged")

    surface = str(body.get("surface") or "channel:webui")
    try:
        params = active_voice_params(
            surface=surface, profile_id=str(body.get("profile_id") or "")
        )
    except VoiceProfileError as exc:
        return web.json_response(
            {"error": exc.message, "reason": exc.reason}, status=exc.status
        )
    if params is None:
        return web.json_response(
            {"error": "No TTS voice selected — choose one in Settings → Models"},
            status=503,
        )
    if not params.get("enabled", False):
        return json_error(
            "tts_disabled",
            message=(
                "Text-to-speech is switched off. Turn on “Speak replies aloud” in "
                "Settings → Speech & Transcription."
            ),
            status=503,
        )

    state.record_spoken(session_name, text)

    chunk_paths: list[str] = []
    final_path: str | None = None
    try:
        async for idx, sentence, wav_bytes in streaming_voice_reply(
            params["provider"],
            text,
            voice=params["voice"],
            speed=params["speed"],
            speech_voice=params["speech_voice"],
        ):

            fd, chunk_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            with open(chunk_path, "wb") as f:
                f.write(wav_bytes)
            chunk_paths.append(chunk_path)

            state.broadcast_ws(
                "voice_chunk",
                {
                    "session": session_name,
                    "index": idx,
                    "sentence": sentence,
                    "audio": base64.b64encode(wav_bytes).decode(),
                },
            )

        if chunk_paths:
            final_path = await stitch_wavs(chunk_paths)
            if final_path:
                _record_generation(params, final_path, text)
                with open(final_path, "rb") as f:
                    final_bytes = f.read()
                state.broadcast_ws(
                    "voice_complete",
                    {
                        "session": session_name,
                        "audio": base64.b64encode(final_bytes).decode(),
                        "chunks": len(chunk_paths),
                    },
                )

        if not chunk_paths:
            return web.json_response(
                {
                    "error": "Speech synthesis produced no audio — check the TTS runtime in Settings → AI & Models"  # noqa: E501
                },
                status=502,
            )
        return web.json_response({"ok": True, "chunks": len(chunk_paths)})
    finally:
        if final_path:
            with contextlib.suppress(OSError):
                os.unlink(final_path)
        for p in chunk_paths:
            with contextlib.suppress(OSError):
                os.unlink(p)

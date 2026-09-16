"""SDK: the diarization provider ABC + result types (core L1).

Stable re-export of ``DiarizationProvider`` (INFERENCE: ``diarize``) / ``DiarizationModel``
/ ``SpeakerTurn`` + ``LocalModelProvider`` (MANAGEMENT). A LOCAL diarization app subclasses
BOTH (inference + local-model management); a hypothetical remote one would subclass only
``DiarizationProvider``. ``ensure_ffmpeg_in_path`` is shared for audio decoding.
"""

from gideon.integrations.diarization.provider import (
    DiarizationModel,
    DiarizationProvider,
    SpeakerTurn,
)
from gideon.integrations.local_models.provider import LocalModelProvider  # noqa: F401
from gideon.integrations.transcribe import ensure_ffmpeg_in_path  # noqa: F401

__all__ = [
    "DiarizationProvider",
    "DiarizationModel",
    "SpeakerTurn",
    "LocalModelProvider",
    "ensure_ffmpeg_in_path",
]

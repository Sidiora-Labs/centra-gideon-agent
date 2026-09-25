"""SDK: the video-generation provider ABC + data types + registry accessor.

Stable re-export of the generic video-gen contract (``gideon.integrations.video_gen``) —
a video-gen app (e.g. fal) implements ``VideoGenProvider`` and registers against
the core registry through these, not the core modules directly.
"""

from gideon.integrations.video_gen.provider import (
    VideoControl,
    VideoGenError,
    VideoGenModel,
    VideoGenProvider,
    VideoResult,
)
from gideon.integrations.video_gen.registry import active_video_gen  # noqa: F401

__all__ = [
    "VideoControl",
    "VideoGenProvider",
    "VideoGenModel",
    "VideoResult",
    "VideoGenError",
    "active_video_gen",
]

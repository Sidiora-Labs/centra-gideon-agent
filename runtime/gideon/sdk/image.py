"""SDK: the image-generation provider ABC + data types + registry accessor.

Stable re-export of the generic image-gen contract (``gideon.image_gen``) —
an image-gen app (e.g. fal) implements ``ImageGenProvider`` and registers against
the core registry through these, not the core modules directly. Provider-agnostic:
fal/openai/replicate/etc. are all implementations of this one contract.
"""

from gideon.image_gen.provider import (  # noqa: F401
    ImageGenError,
    ImageGenModel,
    ImageGenProvider,
    ImageResult,
)
from gideon.image_gen.registry import active_image_gen  # noqa: F401

__all__ = [
    "ImageGenProvider",
    "ImageGenModel",
    "ImageResult",
    "ImageGenError",
    "active_image_gen",
]

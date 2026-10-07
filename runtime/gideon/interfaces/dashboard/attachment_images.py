"""Compatibility module for the shared attachment implementation.

The alias preserves its process singleton and existing private patch seams.
"""

import sys

from gideon.integrations import attachment_images as _implementation
from gideon.integrations.attachment_images import (
    ALLOWED_MEDIA_TYPES as ALLOWED_MEDIA_TYPES,
    MAX_EDGE_PX as MAX_EDGE_PX,
    MAX_PART_BYTES as MAX_PART_BYTES,
    _FORMATS as _FORMATS,
    _fit as _fit,
    _encode as _encode,
    is_image_attachment as is_image_attachment,
    image_part_url as image_part_url,
)

sys.modules[__name__] = _implementation

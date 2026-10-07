"""Compatibility module for the shared attachment implementation.

The alias preserves its process singleton and existing private patch seams.
"""

import sys

from gideon.integrations import attachment_images as _implementation

ALLOWED_MEDIA_TYPES = _implementation.ALLOWED_MEDIA_TYPES
MAX_EDGE_PX = _implementation.MAX_EDGE_PX
MAX_PART_BYTES = _implementation.MAX_PART_BYTES
_FORMATS = _implementation._FORMATS
_fit = _implementation._fit
_encode = _implementation._encode
is_image_attachment = _implementation.is_image_attachment
image_part_url = _implementation.image_part_url

sys.modules[__name__] = _implementation

"""Compatibility module for the shared attachment implementation.

The alias preserves its process singleton and existing private patch seams.
"""

import sys

from gideon.integrations import attachment_extract as _implementation
from gideon.integrations.attachment_extract import (
    AttachmentExtractor as AttachmentExtractor,
    display_name as display_name,
    get_extractor as get_extractor,
    _INSTANCE as _INSTANCE,
    _MAX_ENTRIES as _MAX_ENTRIES,
    _MAX_TEXT_CHARS as _MAX_TEXT_CHARS,
)

sys.modules[__name__] = _implementation

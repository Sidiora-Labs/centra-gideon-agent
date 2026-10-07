"""Compatibility module for the shared attachment implementation.

The alias preserves its process singleton and existing private patch seams.
"""

import sys

from gideon.integrations import attachment_extract as _implementation

AttachmentExtractor = _implementation.AttachmentExtractor
display_name = _implementation.display_name
get_extractor = _implementation.get_extractor
_INSTANCE = _implementation._INSTANCE
_MAX_ENTRIES = _implementation._MAX_ENTRIES
_MAX_TEXT_CHARS = _implementation._MAX_TEXT_CHARS

sys.modules[__name__] = _implementation

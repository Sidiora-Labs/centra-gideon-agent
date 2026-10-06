"""Core contracts available to app manifests."""

import re

FEATURE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
CORE_FEATURE_ADMISSION = "core-feature-admission"
APP_LAUNCH_DISCLOSURE = "app-launch-disclosure"
MANIFEST_BOOLEANS = "manifest-booleans"
CLOSING_STREAMS = "closing-streams"
CUT_OFF_ANSWERS = "cut-off-answers"
CORE_FEATURES = frozenset(
    {CORE_FEATURE_ADMISSION, APP_LAUNCH_DISCLOSURE, MANIFEST_BOOLEANS, CLOSING_STREAMS, CUT_OFF_ANSWERS}
)


def core_has(feature: str) -> bool:
    return feature in CORE_FEATURES

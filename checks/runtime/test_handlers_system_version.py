"""The gateway version travels in the /api/system static payload.

The dashboard no longer carries a version pill; the shell's SystemWidget card
reads the version straight from /api/system, so the static info block must
expose it.
"""

import gideon


def test_static_system_info_includes_gateway_version() -> None:
    from gideon.interfaces.dashboard import handlers_system

    handlers_system._STATIC_SYSTEM_INFO = None
    info = handlers_system._get_static_system_info()

    assert info["version"] == gideon.__version__

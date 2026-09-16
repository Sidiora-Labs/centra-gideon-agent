"""Shared process search paths and desktop-environment detection."""

import os
import sys
from pathlib import Path

_PROC_VERSION = "/proc/version"
_EXTRA_PATH_DIRS = (
    "{home}/.local/bin",
    "{home}/.npm-packages/bin",
    "{home}/.local/share/mise/shims",
)


def _is_wsl() -> bool:
    try:
        kernel = Path(_PROC_VERSION).read_text(encoding="utf-8")
    except OSError:
        return False
    return "microsoft" in kernel.lower()


def browser_available() -> bool:
    visible = any(os.environ.get(name) for name in ("DISPLAY", "WAYLAND_DISPLAY"))
    remote = any(os.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT"))
    return sys.platform == "darwin" or visible or not remote


def augmented_path(base_path: str = "") -> str:
    home = os.path.expanduser("~")
    prefixes = tuple(pattern.format(home=home) for pattern in _EXTRA_PATH_DIRS)
    return os.pathsep.join((*prefixes, base_path) if base_path else prefixes)

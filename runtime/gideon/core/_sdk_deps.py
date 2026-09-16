"""Load optional provider SDKs with an actionable package installation hint."""

from __future__ import annotations

import importlib
from types import ModuleType


class MissingSDKError(ImportError):
    """A requested provider dependency is unavailable."""


def _sdk_remedy(module: str, extra: str, feature: str | None) -> str:
    context = f" required by {feature}" if feature else ""
    requirement = f"gideon-agent-harness[{extra}]"
    return " ".join(
        (
            f"The {module!r} SDK is not installed{context}.",
            f"Install it with `pip install '{requirement}'` (or `uv pip install '{requirement}'`).",
            "If you installed a branded provider app, reinstalling the app will pull it in.",
            "Run `gideon doctor` to check provider dependencies.",
        )
    )


def require_sdk(module: str, extra: str, *, feature: str | None = None) -> ModuleType:
    try:
        dependency = importlib.import_module(module)
    except ModuleNotFoundError as failure:
        raise MissingSDKError(_sdk_remedy(module, extra, feature)) from failure
    return dependency

"""Optional-SDK import guard (plan 34 T1.4).

``require_sdk`` backs the lazy imports in the hosted-provider adapters after the
``openai`` / ``anthropic`` SDKs were demoted out of core dependencies. These
tests pin the two behaviours the adapters rely on: a present SDK imports
transparently, and an absent one raises a ``MissingSDKError`` that (a) names the
exact ``pip install gideon-agent-harness[<extra>]`` remedy and (b) is still catchable by
existing ``except ImportError`` guards.
"""

from __future__ import annotations

import sys

import pytest

from gideon.core._sdk_deps import MissingSDKError, require_sdk


def test_require_sdk_returns_module_when_present() -> None:
    mod = require_sdk("sys", "irrelevant-extra")
    assert mod is sys


def test_require_sdk_raises_missing_with_remedy() -> None:
    with pytest.raises(MissingSDKError) as exc:
        require_sdk("gideon_definitely_absent_pkg", "openai")
    msg = str(exc.value)
    assert "gideon_definitely_absent_pkg" in msg
    assert "pip install 'gideon-agent-harness[openai]'" in msg
    assert "gideon doctor" in msg


def test_missing_sdk_error_is_import_error() -> None:
    assert issubclass(MissingSDKError, ImportError)
    with pytest.raises(ImportError):
        require_sdk("gideon_definitely_absent_pkg", "anthropic")


def test_feature_phrase_included_when_given() -> None:
    with pytest.raises(MissingSDKError) as exc:
        require_sdk(
            "gideon_definitely_absent_pkg",
            "openai",
            feature="the OpenAI chat provider",
        )
    assert "required by the OpenAI chat provider" in str(exc.value)

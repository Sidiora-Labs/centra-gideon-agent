"""Wave 5 supervised modules, native authority, and MCP acceptance."""

from __future__ import annotations

import os

import pytest

from checks.hypermid.verify_wave_05 import run


pytestmark = pytest.mark.skipif(
    os.environ.get("HYPERMID_REAL_MODEL_TEST") != "1",
    reason="requires explicitly enabled Centra and real supervised process journeys",
)


def test_supervised_module_native_authority_and_mcp_lifecycle(tmp_path) -> None:
    result = run(tmp_path)
    assert result["daemon_mcp_bridge"] is True
    assert result["native_tool_provider"] is True
    assert result["approval_count"] == 2
    assert result["terminal_count"] == 2
    assert result["cancel_state"] == "unknown"
    assert result["session_evicted"] is True

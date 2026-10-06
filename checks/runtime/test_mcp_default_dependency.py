"""The MCP client ships in the OSS default installation."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_mcp_sdk_is_a_default_runtime_dependency():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert any(
        item.split(">", 1)[0].split("=", 1)[0].strip().lower() == "mcp"
        for item in project["dependencies"]
    )

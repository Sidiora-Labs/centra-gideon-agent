from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path


def inspect_bundle(
    root: Path, inventory: dict, *, require_console: bool = True
) -> dict:
    if inventory.get("version") != 1 or not inventory.get("files"):
        raise ValueError("missing runtime file inventory")
    if require_console and not inventory.get("console"):
        raise ValueError("console assets missing from inventory")
    root = root.resolve()
    paths = set()
    for entry in inventory["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or entry["path"] in paths:
            raise ValueError("invalid or duplicate bundled path")
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError("bundled path escapes runtime root")
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"bundled file hash mismatch: {relative}")
        paths.add(entry["path"])
    required = {
        "gideon/security/baseline_denylist.json",
        "gideon/integrations/mcp_core.py",
    }
    if require_console:
        required.add("gideon/static/dist/index.html")
    if not required.issubset(paths):
        raise ValueError("required runtime resources absent")
    modules = inventory.get("sdk_modules") or []
    if not modules or not {"gideon.sdk.memory", "gideon.sdk.manifest"}.issubset(
        modules
    ):
        raise ValueError("SDK module inventory missing")
    for module in modules:
        if not module.startswith("gideon.sdk."):
            raise ValueError("invalid SDK module inventory")
        importlib.import_module(module)
    from gideon.integrations.mcp_core import _aggregated_list_tools, run_mcp_core_server

    tools = _aggregated_list_tools()
    if not callable(run_mcp_core_server) or not any(
        tool["name"] == "skill_invoke" for tool in tools
    ):
        raise ValueError("core MCP tools missing")
    return {"files": len(paths), "sdk_modules": len(modules), "mcp_tools": len(tools)}


def main() -> None:
    from gideon.operations.self_update import detect_install_kind

    if not getattr(sys, "frozen", False):
        raise SystemExit("desktop smoke requires the packaged backend")
    root = Path(getattr(sys, "_MEIPASS"))
    inventory = json.loads(
        (root / "gideon/runtime-bundle-manifest.json").read_text(encoding="utf-8")
    )
    result = inspect_bundle(root, inventory)
    result["install_kind"] = detect_install_kind()
    if result["install_kind"] != "desktop":
        raise SystemExit("frozen runtime did not identify as desktop")
    print(json.dumps(result))

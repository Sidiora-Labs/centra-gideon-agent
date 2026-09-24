from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def smoke(backend: Path) -> dict:
    backend = backend.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="gideon-desktop-smoke-") as directory:
        env = {
            **os.environ,
            "GIDEON_HOME": directory,
            "GIDEON_WORKSPACE": str(Path(directory) / "workspace"),
        }
        env.pop("GIDEON_INSTALL_KIND", None)
        result = subprocess.run(
            [str(backend), "--desktop-smoke"],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        report = json.loads(result.stdout)
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "gideon-desktop-smoke", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        protocol = subprocess.run(
            [str(backend), "mcp-core"],
            env=env,
            input="".join(json.dumps(message) + "\n" for message in messages),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        replies = {
            reply["id"]: reply
            for line in protocol.stdout.splitlines()
            if (reply := json.loads(line)).get("id") in (1, 2)
        }
        if any(
            identifier not in replies or "error" in replies[identifier]
            for identifier in (1, 2)
        ):
            raise RuntimeError("packaged MCP handshake failed")
        tools = replies[2].get("result", {}).get("tools", [])
        if not any(tool.get("name") == "skill_invoke" for tool in tools):
            raise RuntimeError("packaged MCP tool inventory is empty or incomplete")
        report["mcp_protocol_tools"] = len(tools)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backend", type=Path)
    print(json.dumps(smoke(parser.parse_args().backend), indent=2))

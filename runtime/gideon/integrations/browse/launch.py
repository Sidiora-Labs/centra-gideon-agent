"""CDP page-target discovery for browser launch paths."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any


def ready_page_target(port: int, deadline: float) -> str | None:
    """Return a page socket that accepts Page commands before ``deadline``."""
    from websockets.sync.client import connect

    while time.monotonic() < deadline:
        try:
            remaining = max(0.01, deadline - time.monotonic())
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=min(1.0, remaining)
            ) as response:
                targets: list[dict[str, Any]] = json.load(response)
            for target in targets:
                ws_url = target.get("webSocketDebuggerUrl")
                if target.get("type") != "page" or not isinstance(ws_url, str):
                    continue
                try:
                    remaining = max(0.01, deadline - time.monotonic())
                    with connect(ws_url, open_timeout=min(1.0, remaining)) as socket:
                        socket.send(
                            json.dumps(
                                {"id": 1, "method": "Page.getFrameTree", "params": {}}
                            )
                        )
                        while time.monotonic() < deadline:
                            remaining = max(0.01, deadline - time.monotonic())
                            reply = json.loads(socket.recv(timeout=min(1.0, remaining)))
                            if reply.get("id") == 1:
                                if "error" not in reply:
                                    return ws_url
                                break
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    return None

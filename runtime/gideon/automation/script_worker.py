"""Standalone scheduled-script process and author API; standard library only."""

from __future__ import annotations

import builtins
import importlib.util
import json
import os
import sys
import traceback
import types
import urllib.request
from pathlib import Path

RESULT_PREFIX = "__GIDEON_SCRIPT_RESULT__"


class Skip(Exception):
    """Finish without a delivery."""


class ScriptNotice(Exception):
    status = ""

    def __init__(self, message: str = "") -> None:
        self.message = message
        Exception.__init__(self, message)


class Done(ScriptNotice):
    """Deliver the result and retire the schedule."""

    status = "done"


class Report(ScriptNotice):
    """Deliver the result and retain the schedule."""

    status = "report"


class GatewayChannel:
    def __init__(self, configuration: dict) -> None:
        self.address = "http://127.0.0.1:%d" % configuration.get("port", 0)
        self.headers = {
            "Content-Type": "application/json",
            "X-Internal-Secret": configuration.get("secret", ""),
            "X-Session-Key": configuration.get("session_key", ""),
        }

    def send(self, path: str, body: dict):
        request = urllib.request.Request(
            url=self.address + path,
            headers=self.headers,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as reply:
            return json.loads(reply.read().decode("utf-8"))


class ScriptContext:
    _channel: GatewayChannel

    def __init__(self, message: str):
        self.message = message

    def notify(self, text, **kwargs):
        return self._channel.send("/api/send-message", dict(text=text) | kwargs)

    def call_tool(self, tool, arguments=None, provider=""):
        payload = dict(tool=tool, provider=provider, arguments=arguments or {})
        return self._channel.send("/api/tools/invoke", payload)


def install_author_api(channel: GatewayChannel) -> None:
    ScriptContext._channel = channel
    exports = {entry.__name__: entry for entry in (Skip, Done, Report, ScriptContext)}
    vars(builtins).update(exports)
    qualified = "gideon.automation.schedule_script"
    if qualified in sys.modules:
        return
    for name in ("gideon", "gideon.automation"):
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
    module = types.ModuleType(qualified)
    vars(module).update(exports)
    sys.modules[qualified] = module


def consume_configuration(path: str) -> dict:
    location = Path(path)
    payload = json.loads(location.read_text(encoding="utf-8"))
    try:
        location.unlink()
    except OSError:
        pass
    return payload


def execute(configuration: dict) -> dict:
    install_author_api(GatewayChannel(configuration))
    try:
        definition = importlib.util.spec_from_file_location(
            "_gideon_cron_script", configuration["script_path"]
        )
        if definition is None or definition.loader is None:
            raise ImportError(f"cannot load script {configuration['script_path']!r}")
        module = importlib.util.module_from_spec(definition)
        definition.loader.exec_module(module)
        entry = getattr(module, configuration["func"], None)
        if not callable(entry):
            return {
                "status": "error",
                "error": f"function {configuration['func']!r} not found",
            }
        value = entry(ScriptContext(configuration.get("message", "")))
        return {"status": "ok", "message": "" if value is None else str(value)[:4000]}
    except Skip:
        return {"status": "skip"}
    except ScriptNotice as notice:
        return {"status": notice.status, "message": notice.message}
    except Exception as failure:
        return {
            "status": "error",
            "error": f"{failure}\n{traceback.format_exc()}"[:4000],
        }


def main() -> None:
    configuration = consume_configuration(sys.argv[1])
    receipt = execute(configuration)
    print("\n" + RESULT_PREFIX + json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()

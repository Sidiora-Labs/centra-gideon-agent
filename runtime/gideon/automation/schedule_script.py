"""Schedule scripts: validate the entry point, stage private input and own the child."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

from gideon.automation.script_worker import Done, Report, Skip
from gideon.core.config import loader as config_loader
from gideon.engine import gateway_base
from gideon.engine.hooks import validate_file_path
from gideon.integrations.mcp_core import _internal_secret
from gideon.security.sandbox import (
    PROFILE_TOOL,
    build_child_env,
    spawn_shim_argv,
    wrap_argv,
)

_DEFAULT_SCRIPT_TIMEOUT = 30
_RESULT_SENTINEL = "__GIDEON_SCRIPT_RESULT__"
_LAUNCHER_SRC = Path(__file__).with_name("script_worker.py").read_text(encoding="utf-8")


def config_dir() -> Path:
    active_home = config_loader.config_dir
    return active_home()


def _crons_dir() -> Path:
    return config_dir().joinpath("crons")


def resolve_script_path(spec: str) -> tuple[Path, str]:
    components = spec.rsplit(":", 1)
    if len(components) != 2:
        raise ValueError("script must be 'path/to/file.py:function'")
    location, symbol = components
    if not location or not symbol.isidentifier():
        raise ValueError("invalid script spec — expected 'file.py:function'")
    if not location.endswith(".py"):
        raise ValueError("script file must be a .py file")
    permitted = validate_file_path(location)
    if permitted is None:
        raise ValueError("script path is not allowed (sensitive or invalid)")
    candidate, root = Path(permitted).resolve(), _crons_dir().resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"script must live under {root}")
    if not candidate.is_file():
        raise ValueError(f"script file not found: {candidate}")
    return candidate, symbol


def _remove_staged(path) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


class ScriptLaunchFiles:
    @staticmethod
    def write(stack: ExitStack, content: str, *, prefix: str, suffix: str) -> str:
        descriptor, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
        stack.callback(_remove_staged, path)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        return path

    @classmethod
    @contextmanager
    def stage(cls, configuration: dict):
        with ExitStack() as ownership:
            private_input = cls.write(
                ownership,
                json.dumps(configuration),
                prefix="gideon-cron-cfg-",
                suffix=".json",
            )
            os.chmod(private_input, 0o600)
            launcher = cls.write(
                ownership, _LAUNCHER_SRC, prefix="gideon-cron-run-", suffix=".py"
            )
            wrapped, sandbox_file = wrap_argv(
                ["python3", launcher, private_input], mode="standard"
            )
            ownership.callback(_remove_staged, sandbox_file)
            yield spawn_shim_argv(wrapped, PROFILE_TOOL)


def _parse_launcher_output(stdout: str) -> dict:
    receipts = (
        line
        for line in reversed(stdout.splitlines())
        if line.startswith(_RESULT_SENTINEL)
    )
    latest = next(receipts, None)
    if latest is not None:
        try:
            return json.loads(latest.removeprefix(_RESULT_SENTINEL))
        except json.JSONDecodeError:
            pass
    return {"status": "error", "error": "no result emitted by script"}


def run_script_sandboxed(
    script_spec: str,
    job_id: str,
    job_message: str,
    timeout: int = 0,
    *,
    session_key: str = "",
) -> dict:
    entry, symbol = resolve_script_path(script_spec)
    deadline = timeout if timeout and timeout > 0 else _DEFAULT_SCRIPT_TIMEOUT
    try:
        port = gateway_base.resolve_port()
    except gateway_base.GatewayBaseUnresolved as failure:
        return {"status": "error", "error": str(failure)}
    configuration = dict(
        script_path=str(entry),
        func=symbol,
        message=job_message,
        secret=_internal_secret(),
        port=port,
        session_key=f"cron:{job_id}",
    )
    with ScriptLaunchFiles.stage(configuration) as command:
        try:
            child = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=deadline,
                env=build_child_env(site="cron-script"),
            )
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"script timed out after {deadline}s"}
        receipt = _parse_launcher_output(child.stdout)
        if (
            receipt.get("status") == "error"
            and child.returncode
            and not receipt.get("error")
        ):
            receipt["error"] = (child.stderr or "script failed")[:4000]
        return receipt

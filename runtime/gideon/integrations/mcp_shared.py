"""Shared helpers for MCP stdio servers (mcp_core, mcp_schedule)."""

import json
import logging
import os
import platform
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from gideon.core.config import loader as config_loader
from gideon.core.constants import JSONRPC_METHOD_NOT_FOUND
from gideon.engine import gateway_base
from gideon.security.sel import sel


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`gideon.core.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_use_content_length = False

_excluded_tools: set[str] | None = None
_last_failure_time: float = 0.0
_last_startup_race_time: float = 0.0
_failure_count: int = 0
_NEGATIVE_CACHE_TTL: float = 60.0
_STARTUP_RACE_CACHE_TTL: float = 5.0
_MAX_WARNING_FAILURES: int = 2


def _resolve_excluded_tools() -> set[str]:
    """Query the gateway for the current session's managedToolPolicy.exclude.

    Returns a set of tool names that should be hidden from this session.
    Caches the result on success only.  On failure:

    - If session key is unavailable (startup race): fail-open, do NOT
      cache, allow retry on next call.  Cannot fail-closed here because
      ACP agent calls tools/list once at session start — if we return an
      empty list, ACP agent permanently believes this MCP server has no
      tools (unrecoverable without session restart).
    - If session key is available but policy call fails: fail-open with
      negative cache (30s) to avoid blocking every tool call with a 5s
      timeout when gateway is persistently unreachable.

    Fail-open is acceptable because:
    1. The SDK already applies managedToolPolicy.exclude as disabledTools
       in the agent config — ACP agent enforces this independently.
    2. The gateway's approval layer provides the authoritative deny gate.
    3. This MCP-level filtering is defense-in-depth for non-ACP-agent
       clients (Claude Code, custom MCP hosts) that skip disabledTools.
    """
    global _excluded_tools, _last_failure_time, _last_startup_race_time, _failure_count
    if _excluded_tools is not None:
        return _excluded_tools

    now = time.monotonic()
    if (_last_failure_time and (now - _last_failure_time) < _NEGATIVE_CACHE_TTL) or (
        _last_startup_race_time
        and (now - _last_startup_race_time) < _STARTUP_RACE_CACHE_TTL
    ):
        sel().log_api_access(
            caller=os.environ.get("GIDEON_SESSION_KEY", "mcp"),
            operation="tool_policy.negative_cache_hit",
            outcome="fail_open",
            source="mcp_shared",
        )
        return set()

    try:
        api_base = gateway_base.resolve_api_base()

        secret = ""
        try:
            secret = (config_dir() / ".local_secret").read_text().strip()
        except Exception:
            pass

        session_key = os.environ.get("GIDEON_SESSION_KEY", "")
        if not session_key:

            def _get_ppid(pid: int) -> int:
                try:
                    if platform.system() == "Linux":
                        for line in (
                            Path(f"/proc/{pid}/status").read_text().splitlines()
                        ):
                            if line.startswith("PPid:"):
                                return int(line.split()[1])
                    else:
                        out = subprocess.check_output(
                            ["ps", "-o", "ppid=", "-p", str(pid)], text=True, timeout=2
                        )
                        return int(out.strip())
                except Exception:
                    pass
                return 0

            cfg_dir = config_dir()
            pid = os.getppid()
            seen: set[int] = set()
            while pid > 1 and pid not in seen:
                seen.add(pid)
                pid_file = cfg_dir / f"session_pid_{pid}.txt"
                if pid_file.exists():
                    session_key = pid_file.read_text(encoding="utf-8").strip()
                    break
                pid = _get_ppid(pid)

        if not session_key:
            _last_startup_race_time = now
            sel().log_api_access(
                caller="mcp",
                operation="tool_policy.no_session_key",
                outcome="fail_open",
                source="mcp_shared",
            )
            return set()

        headers: dict[str, str] = {"X-Internal-Secret": secret}
        headers["X-Session-Key"] = session_key

        req = urllib.request.Request(
            f"{api_base}/api/session-tool-policy",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                policy = json.loads(resp.read())
        except urllib.error.HTTPError as http_exc:
            if http_exc.code == 404:
                _last_startup_race_time = now
                sel().log_api_access(
                    caller=os.environ.get("GIDEON_SESSION_KEY", "mcp"),
                    operation="tool_policy.agent_not_resolved",
                    outcome="fail_open",
                    source="mcp_shared",
                    resources=f"session_key={session_key}",
                )
                return set()
            raise

        exclude = policy.get("exclude", [])
        if isinstance(exclude, list):
            _excluded_tools = {t for t in exclude if isinstance(t, str)}
        else:
            _excluded_tools = set()
        return _excluded_tools
    except Exception as exc:
        _last_failure_time = time.monotonic()
        _failure_count += 1
        if _failure_count <= _MAX_WARNING_FAILURES:
            logger.warning(
                "Tool policy resolution failed (%s), fail-open for %.0fs (defense-in-depth bypass)",
                exc.__class__.__name__,
                _NEGATIVE_CACHE_TTL,
                exc_info=True,
            )
        elif _failure_count == _MAX_WARNING_FAILURES + 1:
            logger.warning(
                "Tool policy resolution still failing — further warnings suppressed; "
                "see audit log for tool_policy.resolution_failed events",
            )
        sel().log_api_access(
            caller=os.environ.get("GIDEON_SESSION_KEY", "mcp"),
            operation="tool_policy.resolution_failed",
            outcome="fail_open",
            source="mcp_shared",
        )
        return set()


def respond(req_id: Any, result: Any, error: dict | None = None) -> None:
    """Write a validated JSON-RPC response to stdout."""
    if req_id is None:
        return
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    from gideon.assurance.validation import ValidationError, validate_jsonrpc_response

    try:
        resp = validate_jsonrpc_response(resp)
    except ValidationError:
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": "Internal error"},
        }
    body = json.dumps(resp)
    if _use_content_length:
        payload = body.encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8")
        sys.stdout.buffer.write(header + payload)
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(body + "\n")
        sys.stdout.flush()


_LINEAGE_KEYS = ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id")


def leaf_env(parent_env: dict[str, str], lineage: dict[str, str]) -> dict[str, str]:
    """The env a compiled batch leaf runs with: the parent's, minus credentials, plus lineage.

    A leaf processes content the parent fetched — the untrusted direction — so the parent's
    credential material is exactly what must not travel with it.

    The secret TEST is `workspace.looks_secret` (over `secrets.SECRET_KEY_HINTS`), reused rather
    than restated: a second list of credential-ish name fragments would drift from the first, and
    the copy that drifted would be the one letting a token through. Filtered by NAME rather than by
    an allowlist of known-safe vars because the parent env is open-ended — an allowlist would drop
    the PATH-shaped vars a subprocess needs and the failure would look like a broken leaf.
    """
    from gideon.automation.workflows.workspace import looks_secret

    filtered = {k: v for k, v in parent_env.items() if not looks_secret(k)}
    filtered.update({k: str(v) for k, v in lineage.items()})
    return filtered


def leaf_tool_denial(name: str) -> str:
    """Why this tool call is refused for a leaf, or "" when it is allowed.

    Two rules, both from `batch_compile` rather than restated here — a second copy of a policy is a
    policy that drifts:

    * ORCHESTRATION tools are denied at EVERY depth. A leaf that can spawn fans out without a
      budget, and the depth counter alone would let it happen once per level.
    * WRITE tools are denied to a research-class leaf. `leaf_tool_posture` decides the posture; this
      is the handler seam that makes it true.

    Depth 0 is the parent: it is not a leaf and is not restricted, so the parent's own
    `subagent_run` still works.
    """
    from gideon.automation.workflows import batch_compile
    from gideon.security.guardrails.policy import profile_for_session, tool_grant_denial

    profile = profile_for_session(os.environ.get("GIDEON_SESSION_KEY", ""))
    denial = tool_grant_denial(name, profile.tool_grants, profile.tool_allowlist)
    if denial:
        return denial

    depth = _leaf_depth()
    if depth <= 0:
        return ""
    if name in batch_compile.ORCHESTRATION_TOOLS:
        return (
            f"{name!r} is an orchestration tool and is denied to a batch leaf at every depth "
            "— a leaf that can fan out again spawns without a budget"
        )
    if _leaf_is_read_only() and batch_compile.is_write_tool(name):
        return (
            f"{name!r} is a write tool and this leaf is capability=research (read-only) "
            "— declare capability=mutating on the leaf if it must write"
        )
    return ""


def _leaf_depth() -> int:
    from gideon.automation.workflows.engine import WF_DEPTH_KEY

    try:
        return int(os.environ.get(WF_DEPTH_KEY, "0") or "0")
    except ValueError:
        return 0


LEAF_READ_ONLY_KEY = "__wf_read_only"


def _leaf_is_read_only() -> bool:
    return os.environ.get(LEAF_READ_ONLY_KEY, "") == "1"


def call_tool_with_logging(
    name: str,
    raw_args: dict[str, Any],
    validate_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
    inner_fn: Callable[[str, dict[str, Any]], str],
    session_key: str,
    downstream_service: str,
) -> str:
    """Validate args, call inner tool function, and log the invocation."""
    from gideon.assurance.validation import ValidationError
    from gideon.security.sel import sel

    denial = leaf_tool_denial(name)
    if denial:
        sel().log_tool_invocation(
            session_key=session_key,
            source="mcp",
            tool_name=name,
            tool_kind=session_key,
            outcome="denied",
            downstream_service=downstream_service,
            error=denial,
        )
        return f"Error: {denial}"

    try:
        args = validate_fn(name, raw_args)
    except ValidationError as e:
        sel().log_tool_invocation(
            session_key=session_key,
            source="mcp",
            tool_name=name,
            tool_kind=session_key,
            outcome="failed",
            downstream_service=downstream_service,
            error=str(e),
        )
        return f"Error: {e}"

    result = inner_fn(name, args)
    outcome = "failed" if result.startswith("Error:") else "completed"
    sel().log_tool_invocation(
        session_key=session_key,
        source="mcp",
        tool_name=name,
        tool_kind=session_key,
        outcome=outcome,
        downstream_service=downstream_service,
        resources=json.dumps(args)[:500] if args else "",
        error=result[:500] if outcome == "failed" else "",
    )
    return result


def _read_message(stdin) -> dict[str, Any] | None:
    """Read one JSON-RPC message, auto-detecting Content-Length vs bare JSON framing.

    Uses stdin.buffer (binary mode) for all reads so that Content-Length byte
    counts are honoured correctly for multi-byte UTF-8 content.
    """
    global _use_content_length
    raw = stdin.buffer
    while True:
        line = raw.readline()
        if not line:
            return None
        line_str = line.decode("utf-8").strip()
        if not line_str:
            continue
        if line_str.lower().startswith("content-length:"):
            try:
                length = int(line_str.split(":", 1)[1].strip())
                _use_content_length = True
                while True:
                    sep = raw.readline()
                    if sep.strip() == b"":
                        break
                body = raw.read(length)
                return json.loads(body.decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                continue
        try:
            return json.loads(line_str)
        except json.JSONDecodeError:
            continue


def run_mcp_stdio_loop(
    server_name: str,
    server_version: str,
    list_tools_fn: Callable[[], list[dict[str, Any]]],
    call_tool_fn: Callable[[str, dict[str, Any]], str],
) -> None:
    """Generic MCP stdio server loop — reads JSON-RPC from stdin, writes to stdout."""
    from gideon.assurance.validation import (
        ValidationError,
        build_tool_response,
        validate_jsonrpc_request,
    )

    while True:
        req = _read_message(sys.stdin)
        if req is None:
            break

        try:
            method, req_id, _params = validate_jsonrpc_request(req)
        except ValidationError:
            continue

        if method == "initialize":
            respond(
                req_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": server_name, "version": server_version},
                },
            )
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            excluded = _resolve_excluded_tools()
            tools = list_tools_fn()
            if excluded:
                tools = [t for t in tools if t.get("name") not in excluded]
            respond(req_id, {"tools": tools})
        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            excluded = _resolve_excluded_tools()
            if tool_name in excluded:
                sel().log_tool_invocation(
                    session_key=os.environ.get("GIDEON_SESSION_KEY", "mcp"),
                    source="mcp",
                    tool_name=tool_name,
                    tool_kind=server_name,
                    outcome="rejected_excluded",
                    error="managedToolPolicy.exclude",
                )
                respond(
                    req_id,
                    build_tool_response(
                        f"Error: tool '{tool_name}' is not available for this agent"
                    ),
                )
            else:
                result_text = call_tool_fn(tool_name, tool_args)
                respond(req_id, build_tool_response(result_text))
        elif req_id is not None:
            respond(
                req_id,
                None,
                error={
                    "code": JSONRPC_METHOD_NOT_FOUND,
                    "message": f"Unknown method: {method}",
                },
            )

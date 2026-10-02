from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .foundation import Id, Scope, Trace


ROLE_PROTOCOL = "hypermid.v1"
MAX_ROLE_LINE_BYTES = 8 * 1024 * 1024
TOOL_ROLE_V1 = "hypermid.tool/v1"
RUNNER_ROLE_V1 = "hypermid.runner/v1"


class RoleProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoleMajor:
    version: str
    ops: tuple[str, ...]
    stability: str

    def to_wire(self) -> dict[str, Any]:
        return {"version": self.version, "ops": list(self.ops), "stability": self.stability}


@dataclass(frozen=True)
class RoleDescriptor:
    implementation_version: str
    majors: tuple[RoleMajor, ...]

    def to_wire(self) -> dict[str, Any]:
        return {
            "implementation_version": self.implementation_version,
            "majors": [major.to_wire() for major in self.majors],
        }


@dataclass(frozen=True)
class RoleRequest:
    role_version: str
    method: str
    params: Mapping[str, Any]
    trace: Trace
    scope: Scope | None = None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {
            "protocol": ROLE_PROTOCOL,
            "role_version": self.role_version,
            "method": self.method,
            "params": dict(self.params),
            "trace": self.trace.to_wire(),
        }
        if self.scope is not None:
            wire["scope"] = self.scope.to_wire()
        return wire


class RoleProcess:
    def __init__(self, command: Sequence[str], state_root: str | Path) -> None:
        self._command = tuple(command)
        self.state_root = Path(state_root)
        if not self.state_root.is_absolute():
            raise RoleProtocolError("role state root must be absolute")
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self._process is not None:
            raise RoleProtocolError("role process is already running")
        self.state_root.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["HYPERMID_ROLE_STATE_ROOT"] = os.fspath(self.state_root)
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )

    def request(self, request: RoleRequest) -> dict[str, Any]:
        payload = _canonical_json(request.to_wire())
        if len(payload.encode("utf-8")) > MAX_ROLE_LINE_BYTES:
            raise RoleProtocolError("role request exceeds eight MiB")
        with self._lock:
            process = self._require_process()
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(payload + "\n")
            process.stdin.flush()
            response_line = process.stdout.readline()
        if not response_line:
            raise RoleProtocolError("role process closed its response route")
        if len(response_line.encode("utf-8")) > MAX_ROLE_LINE_BYTES:
            raise RoleProtocolError("role response exceeds eight MiB")
        response = json.loads(response_line)
        if not isinstance(response, dict) or response.get("request_id") != request.trace.request_id:
            raise RoleProtocolError("role response does not match the request")
        if ("result" in response) == ("error" in response):
            raise RoleProtocolError("role response must contain exactly one terminal outcome")
        return response

    def kill(self) -> int:
        process = self._require_process()
        process.kill()
        code = process.wait(timeout=5)
        self._process = None
        return code

    def restart(self) -> None:
        if self._process is not None:
            raise RoleProtocolError("running role must be killed before restart")
        self.start()

    def close(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None

    def _require_process(self) -> subprocess.Popen[str]:
        if self._process is None or self._process.poll() is not None:
            raise RoleProtocolError("role process is not running")
        return self._process

    def __enter__(self) -> RoleProcess:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def structural_schema_digest(schema: Mapping[str, Any]) -> str:
    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip(child)
                for key, child in value.items()
                if key not in {"description", "$comment", "examples", "title"}
            }
        if isinstance(value, list):
            return [strip(child) for child in value]
        return value

    return hashlib.sha256(_canonical_json(strip(dict(schema))).encode("utf-8")).hexdigest()


class LocalRoleModule:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.state_root.mkdir(parents=True, exist_ok=True)
        (self.state_root / "points").mkdir(exist_ok=True)
        self.state_file = self.state_root / "role-state.json"

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        trace = request.get("trace")
        request_id = trace.get("request_id") if isinstance(trace, dict) else None
        if not isinstance(request_id, str):
            return self._error("invalid", "INVALID_REQUEST", "trace request_id is required")
        if request.get("protocol") != ROLE_PROTOCOL:
            return self._error(request_id, "UNSUPPORTED_PROTOCOL", "unsupported role protocol")
        role = request.get("role_version")
        method = request.get("method")
        params = request.get("params")
        if not isinstance(params, dict):
            return self._error(request_id, "INVALID_REQUEST", "params must be an object")
        try:
            if role == TOOL_ROLE_V1:
                result = self._tool(method, params)
            elif role == RUNNER_ROLE_V1:
                result = self._runner(method, params)
            else:
                return self._error(request_id, "UNSUPPORTED_ROLE", "unsupported role version")
        except RoleProtocolError as exc:
            return self._error(request_id, "ROLE_REFUSED", str(exc))
        return {"request_id": request_id, "result": result}

    def _tool(self, method: Any, params: Mapping[str, Any]) -> Any:
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File to digest"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        entry = {
            "name": "hypermid.file_digest",
            "schema_digest": structural_schema_digest(schema),
            "semantics": 1,
            "capabilities": ["filesystem_read"],
            "result_ops": [],
            "description": "Compute the SHA-256 digest of a local file",
            "input_schema": schema,
        }
        if method == "describe":
            return RoleDescriptor("1.0.0", (RoleMajor(TOOL_ROLE_V1, ("describe", "catalog", "call"), "stable"),)).to_wire()
        if method == "catalog":
            bound = {
                "preset": params.get("preset"), "parameters": params.get("parameters", {}),
                "composition": params.get("composition"), "tools": [entry],
                "system_text": "Local file tools" if params.get("system_text") == "include" else None,
                "session_capabilities": ["synchronous"],
            }
            return {
                "generation": 1,
                "catalog_digest": hashlib.sha256(_canonical_json(bound).encode()).hexdigest(),
                "composition_digest": hashlib.sha256(_canonical_json(params.get("composition")).encode()).hexdigest(),
                "tools": [] if params.get("digest_only") else [entry],
                "system_text": bound["system_text"],
                "session_capabilities": ["synchronous"],
            }
        if method == "call":
            pin = params.get("schema_pin")
            expected = {"tool_name": entry["name"], "schema_digest": entry["schema_digest"], "semantics": 1}
            if pin != expected:
                raise RoleProtocolError("schema pin is stale or incompatible")
            arguments = params.get("arguments")
            path = arguments.get("path") if isinstance(arguments, dict) else None
            if not isinstance(path, str):
                raise RoleProtocolError("path is required")
            data = Path(path).read_bytes()
            return {"kind": "final", "result": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}}
        raise RoleProtocolError("unsupported tool operation")

    def _runner(self, method: Any, params: Mapping[str, Any]) -> Any:
        state = self._load_state()
        if method == "describe":
            return {
                **RoleDescriptor("1.0.0", (RoleMajor(RUNNER_ROLE_V1, ("describe", "send", "transcript"), "alpha"),)).to_wire(),
                "capabilities": ["transcript_reads", "queue", "run_results", "streaming", "dispatch_attribution"],
            }
        if method == "send":
            send_id = params.get("send_id")
            content = params.get("content")
            if not isinstance(send_id, str) or not isinstance(content, str):
                raise RoleProtocolError("send_id and string content are required")
            prior = state["sends"].get(send_id)
            if prior is not None:
                if prior != content:
                    raise RoleProtocolError("send Id was reused with different content")
                return {"outcome": "duplicate", "cursor": state["cursor"]}
            ordinal = len(state["messages"])
            state["messages"].append({"message_id": f"message-{ordinal + 1}", "ordinal": ordinal, "role": "user", "content": content})
            state["sends"][send_id] = content
            state["cursor"]["sequence"] += 1
            self._store_state(state)
            (self.state_root / "points" / "owner-send-recorded").write_text(send_id, encoding="utf-8")
            return {"outcome": "accepted", "cursor": state["cursor"]}
        if method == "transcript":
            return {
                "lineage_id": state["lineage_id"] if state["messages"] else None,
                "messages": state["messages"],
                "next_ordinal": len(state["messages"]),
                "cursor": state["cursor"],
                "head": {
                    "lineage_id": state["lineage_id"] if state["messages"] else None,
                    "next_ordinal": len(state["messages"]),
                    "last_message_id": state["messages"][-1]["message_id"] if state["messages"] else None,
                    "digest": hashlib.sha256(_canonical_json(state["messages"]).encode()).hexdigest(),
                },
            }
        raise RoleProtocolError("unsupported runner operation")

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"lineage_id": "lineage-1", "cursor": {"epoch": 1, "sequence": 0}, "messages": [], "sends": {}}
        value = json.loads(self.state_file.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RoleProtocolError("durable role state is malformed")
        return value

    def _store_state(self, state: Mapping[str, Any]) -> None:
        temporary = self.state_file.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(_canonical_json(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_file)

    @staticmethod
    def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
        return {"request_id": request_id, "error": {"code": code, "message": message, "retryable": False}}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def serve_stdio(state_root: Path) -> int:
    module = LocalRoleModule(state_root)
    for line in sys.stdin:
        if len(line.encode("utf-8")) > MAX_ROLE_LINE_BYTES:
            return 2
        try:
            request = json.loads(line)
            response = module.dispatch(request) if isinstance(request, dict) else LocalRoleModule._error("invalid", "INVALID_REQUEST", "request must be an object")
        except (json.JSONDecodeError, UnicodeError):
            response = LocalRoleModule._error("invalid", "INVALID_REQUEST", "request is not valid JSON")
        sys.stdout.write(_canonical_json(response) + "\n")
        sys.stdout.flush()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("serve", nargs="?")
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args(argv)
    state_root = args.state_root or Path(os.environ["HYPERMID_ROLE_STATE_ROOT"])
    return serve_stdio(state_root)


if __name__ == "__main__":
    raise SystemExit(main())

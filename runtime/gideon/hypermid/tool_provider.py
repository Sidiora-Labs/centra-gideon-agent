from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from gideon.integrations.tool_providers.base import RiskLevel

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from gideon.engine.task_modes import infer_risk_from_name
from gideon.integrations.tool_providers.base import (
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

from .foundation import EffectState, Id, Scope, Trace
from .roles import ROLE_PROTOCOL, TOOL_ROLE_V1, RoleProcess, RoleRequest


class HypermidToolViolation(ValueError):
    pass


class CancellationState(str, Enum):
    WITHDRAWN = "withdrawn"
    ALREADY_STARTED = "already_started"
    COMPLETED = "completed"
    REFUSED = "refused"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CancellationOutcome:
    state: CancellationState
    effect_state: EffectState


@dataclass(frozen=True, slots=True)
class ToolCapability:
    name: str
    schema_digest: str
    semantics: int
    capabilities: tuple[str, ...]
    result_operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    connected: bool
    catalog_generation: int | None
    session_capabilities: tuple[str, ...]
    active_calls: int


@dataclass(frozen=True, slots=True)
class _CatalogTool:
    definition: ToolDefinition
    capability: ToolCapability
    schema_pin: Mapping[str, Any]


class HypermidRoleToolProvider(ToolProvider):
    def __init__(
        self,
        *,
        route: RoleProcess,
        scope: Scope,
        provider_name: str,
        display_name: str,
        carrier_id: Id,
        preset: str = "default",
        parameters: Mapping[str, Any] | None = None,
        composition: Mapping[str, Any] | None = None,
    ) -> None:
        if not provider_name or not display_name:
            raise HypermidToolViolation("provider identity is required")
        self._route = route
        self._scope = scope
        self._name = provider_name
        self._display_name = display_name
        self._carrier_id = carrier_id
        self._catalog_request = {
            "preset": preset,
            "parameters": dict(parameters or {}),
            "composition": dict(composition or {}),
            "system_text": "omit",
            "digest_only": False,
        }
        self._catalog: dict[str, _CatalogTool] = {}
        self._catalog_generation: int | None = None
        self._session_capabilities: tuple[str, ...] = ()
        self._active_calls: set[str] = set()
        self._catalog_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def connected(self) -> bool:
        return self._route.running

    @property
    def capabilities(self) -> tuple[ToolCapability, ...]:
        return tuple(item.capability for item in self._catalog.values())

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            connected=self.connected,
            catalog_generation=self._catalog_generation,
            session_capabilities=self._session_capabilities,
            active_calls=len(self._active_calls),
        )

    async def list_tools(self) -> list[ToolDefinition]:
        async with self._catalog_lock:
            response = await self._request("catalog", self._catalog_request)
            result = _require_result(response)
            generation = _positive_integer(
                result.get("generation"), "catalog generation"
            )
            tools = result.get("tools")
            session_capabilities = result.get("session_capabilities", [])
            if not isinstance(tools, list) or not isinstance(
                session_capabilities, list
            ):
                raise HypermidToolViolation("tool catalog is malformed")
            parsed: dict[str, _CatalogTool] = {}
            for raw in tools:
                item = _catalog_tool(raw, self._name)
                if item.definition.name in parsed:
                    raise HypermidToolViolation("tool catalog contains duplicate names")
                parsed[item.definition.name] = item
            if not all(isinstance(value, str) for value in session_capabilities):
                raise HypermidToolViolation("session capabilities are malformed")
            self._catalog = parsed
            self._catalog_generation = generation
            self._session_capabilities = tuple(sorted(set(session_capabilities)))
            return [item.definition for item in parsed.values()]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if not isinstance(arguments, dict):
            return _failure(
                "tool arguments must be an object",
                EffectState.NOT_STARTED,
            )
        if not self._catalog:
            try:
                await self.list_tools()
            except Exception as exc:
                return _failure(
                    f"Hypermid tool discovery failed: {str(exc)[:200]}",
                    EffectState.NOT_STARTED,
                )
        tool = self._catalog.get(tool_name)
        if tool is None:
            return _failure(
                "tool is not owned by this Hypermid provider", EffectState.NOT_STARTED
            )
        call_key = f"call-{uuid4().hex}"
        self._active_calls.add(call_key)
        try:
            response = await self._request(
                "call",
                {
                    "scope": self._scope.to_wire(),
                    "carrier_id": str(self._carrier_id),
                    "call_key": call_key,
                    "schema_pin": dict(tool.schema_pin),
                    "arguments": arguments,
                },
            )
        except Exception as exc:
            return _failure(
                f"Hypermid role route ended after dispatch: {str(exc)[:200]}",
                EffectState.UNKNOWN,
                call_key=call_key,
            )
        finally:
            self._active_calls.discard(call_key)
        if "error" in response:
            return _error_result(response["error"], call_key)
        result = response.get("result")
        if not isinstance(result, Mapping) or result.get("kind") != "final":
            return _failure(
                "Hypermid tool omitted its terminal frame",
                EffectState.UNKNOWN,
                call_key=call_key,
            )
        value = result.get("result")
        output = (
            value
            if isinstance(value, str)
            else json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
        )
        return ToolResult(
            True,
            output=output,
            metadata={
                "call_key": call_key,
                "effect_state": EffectState.COMMITTED.value,
            },
        )

    async def cancel(self, call_key: str, requester_id: Id) -> CancellationOutcome:
        if (
            not call_key
            or len(call_key) > 256
            or any(character.isprintable() is False for character in call_key)
        ):
            raise HypermidToolViolation("call key is invalid")
        response = await self._request(
            "withdraw",
            {
                "scope": self._scope.to_wire(),
                "requester_id": str(requester_id),
                "carrier_id": str(self._carrier_id),
                "call_key": call_key,
            },
        )
        if "error" in response:
            error = response["error"]
            effect = (
                _effect_state(error.get("effect_state"), EffectState.NOT_STARTED)
                if isinstance(error, Mapping)
                else EffectState.NOT_STARTED
            )
            return CancellationOutcome(CancellationState.REFUSED, effect)
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise HypermidToolViolation("withdrawal response is malformed")
        try:
            state = CancellationState(result.get("outcome"))
        except ValueError as exc:
            raise HypermidToolViolation("withdrawal outcome is unknown") from exc
        effect = {
            CancellationState.WITHDRAWN: EffectState.NOT_STARTED,
            CancellationState.REFUSED: EffectState.NOT_STARTED,
            CancellationState.ALREADY_STARTED: EffectState.UNKNOWN,
            CancellationState.COMPLETED: EffectState.COMMITTED,
            CancellationState.UNKNOWN: EffectState.UNKNOWN,
        }[state]
        return CancellationOutcome(state, effect)

    async def _request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        trace = Trace(Id(f"trace-{uuid4().hex}"), Id(f"request-{uuid4().hex}"))
        request = RoleRequest(
            TOOL_ROLE_V1,
            method,
            params,
            trace,
            self._scope,
        )
        response = await asyncio.to_thread(self._route.request, request)
        if not isinstance(response, dict):
            raise HypermidToolViolation("role response is malformed")
        return response


def _catalog_tool(value: Any, provider: str) -> _CatalogTool:
    if not isinstance(value, Mapping):
        raise HypermidToolViolation("tool entry is malformed")
    name = value.get("name")
    description = value.get("description", "")
    schema = value.get("input_schema")
    schema_digest = value.get("schema_digest")
    semantics = value.get("semantics")
    capabilities = value.get("capabilities")
    result_operations = value.get("result_ops", [])
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(description, str)
        or not isinstance(schema, dict)
        or not isinstance(schema_digest, str)
        or len(schema_digest) != 64
        or not isinstance(semantics, int)
        or isinstance(semantics, bool)
        or semantics < 1
        or not isinstance(capabilities, list)
        or not all(isinstance(item, str) for item in capabilities)
        or not isinstance(result_operations, list)
        or not all(
            item in {"prepend", "append", "replace"} for item in result_operations
        )
    ):
        raise HypermidToolViolation("tool entry is malformed")
    definition = ToolDefinition(
        name=name,
        description=description,
        provider=provider,
        parameters=schema,
        requires_approval=True,
        risk_level=cast("RiskLevel", infer_risk_from_name(name)),
    )
    capability = ToolCapability(
        name=name,
        schema_digest=schema_digest,
        semantics=semantics,
        capabilities=tuple(sorted(set(capabilities))),
        result_operations=tuple(sorted(set(result_operations))),
    )
    return _CatalogTool(
        definition,
        capability,
        {"tool_name": name, "schema_digest": schema_digest, "semantics": semantics},
    )


def _require_result(response: Mapping[str, Any]) -> Mapping[str, Any]:
    if "error" in response:
        error = response["error"]
        message = (
            error.get("message")
            if isinstance(error, Mapping)
            else "role refused request"
        )
        raise HypermidToolViolation(str(message)[:300])
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise HypermidToolViolation("role result is malformed")
    return result


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HypermidToolViolation(f"{label} is invalid")
    return value


def _effect_state(value: Any, fallback: EffectState) -> EffectState:
    try:
        return EffectState(value)
    except (TypeError, ValueError):
        return fallback


def _error_result(value: Any, call_key: str) -> ToolResult:
    if not isinstance(value, Mapping):
        return _failure(
            "Hypermid role returned a malformed error",
            EffectState.UNKNOWN,
            call_key=call_key,
        )
    return _failure(
        str(value.get("message") or value.get("code") or "Hypermid tool failed")[:300],
        _effect_state(value.get("effect_state"), EffectState.NOT_STARTED),
        call_key=call_key,
        code=str(value.get("code") or "ROLE_ERROR")[:64],
    )


def _failure(
    message: str,
    effect_state: EffectState,
    *,
    call_key: str | None = None,
    code: str = "HYPERMID_TOOL_ERROR",
) -> ToolResult:
    metadata: dict[str, Any] = {"code": code, "effect_state": effect_state.value}
    if call_key is not None:
        metadata["call_key"] = call_key
    return ToolResult(False, error=message, metadata=metadata)


__all__ = [
    "CancellationOutcome",
    "CancellationState",
    "HypermidRoleToolProvider",
    "HypermidToolViolation",
    "ProviderHealth",
    "ToolCapability",
]

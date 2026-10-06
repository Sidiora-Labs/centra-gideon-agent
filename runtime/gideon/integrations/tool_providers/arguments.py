"""Pure, conservative checks against a tool's declared input schema."""
from __future__ import annotations

from itertools import islice
from typing import Any

_REACH = {"$ref", "$dynamicRef", "allOf", "properties", "items", "prefixItems"}


def _skip(*args: Any) -> None:
    return None


def problems(arguments: Any, schema: Any, keyword: str) -> list[str]:
    if not isinstance(schema, dict) or not schema:
        return []
    try:
        import jsonschema
        from referencing import Registry
        base = jsonschema.validators.validator_for(schema)
        base.check_schema(schema)
        checker = jsonschema.validators.extend(base, {
            name: _skip for name in base.VALIDATORS if name not in _REACH | {keyword}
        })(schema, registry=Registry())
        errors = islice((e for e in checker.iter_errors(arguments) if e.validator == keyword), 5)
        result = []
        for error in errors:
            where = "/".join(str(p) for p in error.absolute_path) or "arguments"
            if keyword == "required":
                missing = [name for name in error.validator_value if isinstance(error.instance, dict) and name not in error.instance]
                detail = "missing required " + ", ".join(str(name) for name in missing)
            else:
                detail = "expected JSON type " + str(error.validator_value)
            result.append(f"{where}: {detail}"[:200])
        return result
    except Exception:
        # Invalid schemas and unavailable references remain the tool's responsibility.
        return []


def argument_refusal(tool: str, arguments: Any, schema: Any, *, types: bool = False) -> str:
    issues = problems(arguments, schema, "required")
    if types:
        issues += problems(arguments, schema, "type")
    if not issues:
        return ""
    return "MCP tool was not run: " + tool + " has invalid arguments. " + "; ".join(issues[:5]) + ". Correct the call using its declared input schema."


def refused_result(reason: str):
    from .base import ToolResult
    return ToolResult(False, error=reason, metadata={"effect_state": "not_started", "not_run": True, "refused_by_tool": True})

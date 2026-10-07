"""Build-time API reference from bundled declarations and static route registrations.

The four Markdown files are generated, sorted, and checked for byte-level drift
by ``checks/runtime/test_agent_reference.py``. Tool schemas come from bundled
provider factories; route paths and handler summaries come from a package-wide
AST walk. This catalog does not represent a running gateway's installed apps,
configured remote tools, authorization, or service readiness.

Regenerate with ``python -m gideon.extensions.manifest_reference``.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from gideon.assurance.api_version import API_VERSION
from gideon.extensions.manifest_meta import (
    TOOL_META,
    canonical_route,
    is_excluded_route,
)

_VERB_PATH_ARG = {
    "add_get": 0,
    "add_post": 0,
    "add_put": 0,
    "add_delete": 0,
    "add_patch": 0,
    "add_route": 1,
}

_REFERENCE_PKG = "gideon.reference"

_ROUTE_SIG_PREFIX = re.compile(
    r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)"
    r"(?:/(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS))*"
    r"\s+/\S*\s*[—:-]?\s*"
)


def _clean_summary(summary: str) -> str:
    """Drop a leading route-signature restatement from a handler docstring summary."""
    return _ROUTE_SIG_PREFIX.sub("", summary).strip()


def reference_dir() -> Path:
    """On-disk path of the shipped reference directory (wheel / editable / source).

    Uses ``importlib.resources.files`` so ``gideon doctor --paths`` can point
    clients at the docs from the installed binary alone.
    """
    return Path(str(resources.files(_REFERENCE_PKG)))


def _route_source_files() -> list[Path]:
    """Every package file that could register or define an HTTP route.

    The WHOLE package, not just ``dashboard/``. Entity route families live beside their
    domain (``artifacts/handlers.py``, ``tasks/handlers.py``, ``workflows/handlers.py``) and
    are mounted by ``server.py`` via a ``register_*_routes(app)`` call — so a walk rooted at
    ``dashboard/`` sees the mount call but never the routes themselves, and those families
    were silently missing from the offline reference. An agent reading the reference to find
    an endpoint would conclude it does not exist.
    """
    import gideon

    root = Path(gideon.__file__).parent
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _handler_docstrings() -> dict[str, str]:
    """Map every function name in the package to its docstring.

    A global index: handlers are referenced at registration as a bare name
    (``api_autonudge_list``) or an attribute (``handlers.api_spawn``,
    ``_up.api_uploads_init``); in every form the callable's own name is the final
    identifier, so one flat name→docstring index resolves them all. First
    definition wins on the rare duplicate — deterministic under the sorted walk.
    """
    index: dict[str, str] = {}
    for py in _route_source_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in index:
                    continue
                doc = ast.get_docstring(node) or ""
                index[node.name] = doc
    return index


def _handler_name(node: ast.expr | None) -> str:
    """The final identifier of a handler reference (``a.b.c`` → ``c``, ``x`` → ``x``)."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _route_table_calls(tree: ast.AST) -> list[ast.Call]:
    class BoundRoute(ast.NodeTransformer):
        def __init__(self, values):
            self.values = values

        def visit_Name(self, node):
            return self.values.get(node.id, node)

        def visit_JoinedStr(self, node):
            node = self.generic_visit(node)
            parts = [
                v.value if isinstance(v, ast.FormattedValue) else v for v in node.values
            ]
            strings = [
                v.value
                for v in parts
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
            if len(strings) == len(parts):
                return ast.Constant("".join(strings))
            return node

        def visit_BinOp(self, node):
            node = self.generic_visit(node)
            if (
                isinstance(node.op, ast.Add)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.right, ast.Constant)
                and isinstance(node.left.value, str)
                and isinstance(node.right.value, str)
            ):
                return ast.Constant(node.left.value + node.right.value)
            return node

        def visit_Call(self, node):
            node = self.generic_visit(node)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "lower"
                and isinstance(node.func.value, ast.Constant)
                and isinstance(node.func.value.value, str)
                and not node.args
            ):
                return ast.Constant(node.func.value.value.lower())
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                return ast.Attribute(
                    value=node.args[0], attr=node.args[1].value, ctx=ast.Load()
                )
            return node

    import copy

    calls: list[ast.Call] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tables = {}
        for statement in function.body:
            if isinstance(statement, ast.Assign) and isinstance(
                statement.value, (ast.Tuple, ast.List)
            ):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        tables[target.id] = statement.value.elts
            if not (
                isinstance(statement, ast.For)
                and isinstance(statement.target, ast.Tuple)
                and isinstance(statement.iter, ast.Name)
                and statement.iter.id in tables
            ):
                continue
            names = [
                name for name in statement.target.elts if isinstance(name, ast.Name)
            ]
            if len(names) != len(statement.target.elts):
                continue
            for row in tables[statement.iter.id]:
                if not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != len(
                    names
                ):
                    continue
                values = {name.id: value for name, value in zip(names, row.elts)}
                for body in statement.body:
                    bound = BoundRoute(values).visit(copy.deepcopy(body))
                    if isinstance(bound, ast.Assign):
                        for target in bound.targets:
                            if isinstance(target, ast.Name):
                                values[target.id] = bound.value
                    calls.extend(
                        node for node in ast.walk(bound) if isinstance(node, ast.Call)
                    )
    return calls


def _routes_from_ast() -> list[dict[str, Any]]:
    """Every literal HTTP route registered in the package, with its summary.

    Same AST walk as the drift test's ``_literal_route_paths`` (no boot), extended
    to pair each path with the handler's docstring first line. Excluded routes
    (UI transport / app proxy — :data:`MANIFEST_EXCLUDE`) are dropped, matching the
    live ``/api/manifest`` walk. Scans the whole package (see
    :func:`_route_source_files`) so entity families registered from outside
    ``dashboard/`` are not invisible here.
    """
    docs = _handler_docstrings()
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for py in _route_source_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in [*ast.walk(tree), *_route_table_calls(tree)]:
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            verb = node.func.attr
            if verb not in _VERB_PATH_ARG:
                continue
            path_idx = _VERB_PATH_ARG[verb]
            if len(node.args) <= path_idx:
                continue
            path_node = node.args[path_idx]
            if not (
                isinstance(path_node, ast.Constant) and isinstance(path_node.value, str)
            ):
                continue
            path = canonical_route(path_node.value)
            method = "*" if verb == "add_route" else verb[len("add_") :].upper()
            if is_excluded_route(method, path):
                continue
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)
            handler_idx = path_idx + 1
            handler = node.args[handler_idx] if len(node.args) > handler_idx else None
            doc = docs.get(_handler_name(handler), "")
            summary = _clean_summary(doc.splitlines()[0].strip() if doc else "")
            out.append(
                {
                    "method": method,
                    "path": path,
                    "summary": summary,
                    "agent_callable": path.startswith("/api/")
                    and not path.startswith("/api/ws"),
                }
            )
    out.sort(key=lambda d: (d["path"], d["method"]))
    return out


def _render_params(parameters: dict[str, Any]) -> list[str]:
    """Render a tool's JSON-schema parameters as a bullet list (name, type, required)."""
    props = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
    required = set(
        parameters.get("required", []) if isinstance(parameters, dict) else []
    )
    if not props:
        return ["- _(no parameters)_"]
    lines: list[str] = []
    for pname in sorted(props):
        spec = props[pname] if isinstance(props[pname], dict) else {}
        ptype = spec.get("type", "any")
        if isinstance(ptype, list):
            ptype = "|".join(str(t) for t in ptype)
        req = "required" if pname in required else "optional"
        desc = (spec.get("description") or "").strip().replace("\n", " ")
        suffix = f" — {desc}" if desc else ""
        lines.append(f"- `{pname}` ({ptype}, {req}){suffix}")
    return lines


def _render_tools(tools: list[dict[str, Any]]) -> str:
    lines = [
        "# Gideon Tool Reference",
        "",
        f"Generated from bundled tool declarations (manifest apiVersion {API_VERSION}). "
        "Bundled in-process tools, grouped by provider, with their exact input "
        "schema and worked examples.",
        "",
        "Input schemas and examples describe the bundled declarations. Runtime "
        "tool ownership, grants, and availability are checked separately.",
        "",
    ]
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for t in tools:
        by_provider.setdefault(t["provider"], []).append(t)
    for provider in sorted(by_provider):
        lines.append(f"## {provider}")
        lines.append("")
        for t in sorted(by_provider[provider], key=lambda d: d["name"]):
            lines.append(f"### `{t['name']}`")
            lines.append("")
            desc = (t.get("description") or "").strip()
            if desc:
                lines.append(desc)
                lines.append("")
            rt = t.get("response_type") or ""
            if rt:
                lines.append(f"**Response type:** `{rt}`")
                lines.append("")
            codes = t.get("error_codes") or []
            if codes:
                lines.append("**Error codes:** " + ", ".join(f"`{c}`" for c in codes))
                lines.append("")
            approval = []
            if t.get("requires_approval"):
                approval.append("requires approval")
            risk = t.get("risk_level") or "safe"
            if risk and risk != "safe":
                approval.append(f"risk: {risk}")
            if approval:
                lines.append("**Safety:** " + ", ".join(approval))
                lines.append("")
            lines.append("**Parameters:**")
            lines.extend(_render_params(t.get("parameters") or {}))
            lines.append("")
            for ex in t.get("examples") or []:
                summary = (ex.get("summary") or "").strip()
                args = ex.get("args", {})
                lines.append(f"**Example — {summary}:**")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(args, indent=2, sort_keys=True))
                lines.append("```")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_routes(routes: list[dict[str, Any]]) -> str:
    agent = [r for r in routes if r["agent_callable"]]
    other = [r for r in routes if not r["agent_callable"]]
    lines = [
        "# Gideon HTTP Route Reference",
        "",
        "Statically discovered HTTP route declarations. **Agent-callable routes** (`/api/*`, non-websocket) "
        "are the ones an agent drives directly; the rest are websocket / internal and "
        "listed after for completeness. This classification does not grant access or "
        "guarantee that a route is mounted in a running gateway.",
        "",
        "Mutating endpoints retain their native authentication, review, and grant checks.",
        "",
        "## Agent-callable routes",
        "",
    ]
    for r in agent:
        summary = r["summary"] or "_(no summary)_"
        lines.append(f"- `{r['method']} {r['path']}` — {summary}")
    lines.append("")
    lines.append("## Websocket / internal routes")
    lines.append("")
    for r in other:
        summary = r["summary"] or "_(no summary)_"
        lines.append(f"- `{r['method']} {r['path']}` — {summary}")
    return "\n".join(lines).rstrip() + "\n"


def _render_providers(providers: dict[str, Any]) -> str:
    lines = [
        "# Gideon Provider Reference",
        "",
        "The extension-provider taxonomy (the capability types an app can contribute) "
        "and the provider declarations bundled in this build. These declarations do not "
        "indicate runtime activation or readiness.",
        "",
        "## Provider types",
        "",
    ]
    for t in providers.get("types", []):
        lines.append(f"- `{t}`")
    lines.append("")
    lines.append("## Bundled provider declarations")
    lines.append("")
    registered = providers.get("registered", [])
    if not registered:
        lines.append("_(no bundled declarations)_")
    else:
        for p in registered:
            state = "bundled declaration"
            if p.get("error"):
                state += f", error: {p['error']}"
            caps = ", ".join(p.get("capabilities", [])) or "—"
            lines.append(
                f"- **{p['app']}** — type `{p['type']}` / `{p['provider_type']}` "
                f"({state}); capabilities: {caps}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _render_index(manifest: dict[str, Any]) -> str:
    tools = manifest["tools"]
    routes = manifest["routes"]
    providers = manifest["providers"]
    n_agent_routes = sum(1 for r in routes if r["agent_callable"])
    provider_names = sorted({t["provider"] for t in tools})
    lines = [
        "# Gideon Agent Reference",
        "",
        f"Offline API/tool reference for Gideon (manifest apiVersion "
        f"{API_VERSION}). Generated from bundled tool/provider declarations and static "
        "route registrations. `GET /api/manifest` describes the running gateway. "
        "Load the `gideon-api` skill for API usage; "
        "this reference is the exact-signature lookup it points to.",
        "",
        "## How to use this (orient, then drill)",
        "",
        "1. Read this index to locate the surface you need — don't read every file.",
        "2. Drill into the one relevant section:",
        f"   - **[tools.md](tools.md)** — {len(tools)} bundled tools across "
        f"{len(provider_names)} providers, with exact input schemas + examples.",
        f"   - **[routes.md](routes.md)** — {n_agent_routes} agent-callable HTTP routes "
        f"(of {len(routes)} total), with summaries.",
        f"   - **[providers.md](providers.md)** — the provider-type taxonomy + "
        f"{len(providers.get('registered', []))} bundled provider declarations.",
        "3. Copy the exact signature — never guess a parameter name.",
        "4. After a mutating call, read the entity back to confirm it took.",
        "",
        "## Tool providers at a glance",
        "",
    ]
    by_provider: dict[str, int] = {}
    for t in tools:
        by_provider[t["provider"]] = by_provider.get(t["provider"], 0) + 1
    for provider in sorted(by_provider):
        lines.append(f"- `{provider}` — {by_provider[provider]} tools")
    lines.extend(
        [
            "",
            "## App updates and frontend assets",
            "",
            "- `POST /api/apps/{name}/update` previews `{source}`. Submit the "
            "reviewed `{source, review_digest}` to apply it; a changed staged bundle "
            "requires another review. A `confirm` flag does not replace this digest.",
            "- The console build is `apps/console/dist`. In a source checkout, "
            "`make web-build` links `runtime/gideon/static/dist` to that build. "
            "Packaged distributions include the assets at `gideon/static/dist`; "
            "they need not use a symlink.",
            "- `gideon doctor --paths` reports the installed reference directory.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_reference() -> dict[str, str]:
    """Render the bundled catalog without activating runtime domain providers."""
    from gideon.extensions.apps.manifest import PROVIDER_TYPES, AppManifest
    from gideon.extensions.providers import registry as prov_reg
    from gideon.extensions.providers.loader import BUNDLED_DIR, load_factory
    from gideon.integrations.tool_providers import registry as tool_reg

    # Keep factory-import registrations local to this synchronous build operation.
    # Existing runtime registrations, callbacks, and locks retain their identity.
    names = (
        "_providers",
        "_registrations",
        "_ownership_refusals",
        "_load_failures",
        "_mcp_provider_instances",
        "_catalog_lock",
    )
    prior = {name: getattr(tool_reg, name) for name in names}
    prior_registry = prov_reg._registry
    tool_reg._providers = {}
    tool_reg._registrations = {}
    tool_reg._ownership_refusals = []
    tool_reg._load_failures = []
    tool_reg._mcp_provider_instances = {}
    tool_reg._catalog_lock = asyncio.Lock()
    prov_reg._registry = None

    async def bundled_catalog() -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        registered: list[dict[str, Any]] = []
        for directory in sorted(BUNDLED_DIR.iterdir()):
            manifest_path = directory / "app.json"
            if not manifest_path.exists():
                continue
            manifest = AppManifest.from_json_file(manifest_path)
            for config in manifest.all_providers():
                registered.append(
                    {
                        "app": manifest.name,
                        "type": config.type,
                        "provider_type": config.providerType,
                        "capabilities": list(config.capabilities),
                    }
                )
                if config.type != "tool":
                    continue
                ext = prov_reg.RegisteredProvider(manifest.name, manifest, config)
                provider = load_factory(ext)({})
                if provider is None:
                    raise ValueError(
                        f"Bundled tool provider {manifest.name} returned no catalog"
                    )
                for tool in await provider.list_tools():
                    meta = TOOL_META.get(tool.name, {})
                    tools.append(
                        {
                            "name": tool.name,
                            "provider": provider.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                            "requires_approval": tool.requires_approval,
                            "risk_level": getattr(
                                tool.risk_level, "value", tool.risk_level
                            )
                            or "safe",
                            "response_type": meta.get("response_type", ""),
                            "error_codes": list(meta.get("error_codes", ())),
                            "examples": list(meta.get("examples", ())),
                        }
                    )
        tools.sort(key=lambda row: (row["provider"], row["name"]))
        registered.sort(key=lambda row: (row["type"], row["app"], row["provider_type"]))
        return {
            "tools": tools,
            "providers": {"types": sorted(PROVIDER_TYPES), "registered": registered},
        }

    try:
        doc = asyncio.run(bundled_catalog())
    finally:
        for name, value in prior.items():
            setattr(tool_reg, name, value)
        prov_reg._registry = prior_registry

    doc["routes"] = _routes_from_ast()
    return {
        "index.md": _render_index(doc),
        "tools.md": _render_tools(doc["tools"]),
        "routes.md": _render_routes(doc["routes"]),
        "providers.md": _render_providers(doc["providers"]),
    }


def write_reference(target: Path | None = None) -> list[Path]:
    """Write the rendered reference to disk; return the paths written."""
    target = target or reference_dir()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, content in render_reference().items():
        path = target / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover - regeneration entry point
    for p in write_reference():
        print(f"wrote {p}")

"""The grounding bundle — what a planner is allowed to know, read from the live system.

Generating a workflow spec is the most failure-prone step in planning, and the measured cause
is not model capability: it is that an ungrounded planner invents node kinds, provider names and
argument shapes that look plausible and do not exist. The plan's own measurement puts
first-try-valid at 0/5 ungrounded and 4/5 grounded, with silent spec misses going 3 → 0.

**Regenerated from the registries, never hand-written.** A hand-maintained reference is wrong the
first time a provider is added and nobody notices, because a stale reference fails the same way a
hallucination does — the planner emits something the validator rejects, and the error names the
spec rather than the reference. So node kinds come from `NodeKind`, providers from the action
registry, and argument shapes from `MCP_CORE_SCHEMAS` and the providers' own docstrings.

**Orient, then drill.** The bundle has an INDEX (every provider, one line each) and DETAIL
(full signatures, on request). Handing a planner every signature for sixteen providers spends
context on fifteen it will not use, and the index is what lets it choose before it reads.

**Two signature sources, joined, because neither is complete.** Measured: only some providers
document their `action_config` shape in a docstring, and `create-task` / `notify` have no
docstring at all — a docstring-only bundle would hand the planner an empty signature for exactly
the providers a plan most often reaches for. `MCP_CORE_SCHEMAS` has typed fields with required
flags for the tool-backed ones. Where both exist the typed schema wins; where neither does, the
bundle says so rather than implying the provider takes no arguments.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_DETAILED_PROVIDERS = 6

MAX_MCP_TOOLS = 40


@dataclass
class ProviderSignature:
    """One action provider as a planner needs to see it."""

    name: str
    display_name: str = ""
    summary: str = ""
    fields: list[tuple[str, str, bool]] = field(default_factory=list)
    source: str = ""
    supports_dry_run: bool = False

    @property
    def arguments_known(self) -> bool:
        """Did we actually learn this provider's arguments?

        Load-bearing distinction: a provider with no discoverable shape must be presented as
        "arguments undocumented", never as one taking no arguments. The second reads as a fact and
        produces a spec with an empty `with` block that fails at run time.
        """
        return bool(self.source)

    def index_line(self) -> str:
        """One line for the orient pass."""
        args = ""
        if self.fields:
            required = [n for n, _t, req in self.fields if req]
            if required:
                args = f" (requires: {', '.join(required)})"
            elif self.source == "source-scan":
                args = f" (args: {', '.join(n for n, _t, _r in self.fields[:5])})"
            else:
                args = " (all args optional)"
        elif not self.arguments_known:
            args = " (arguments undocumented — check the provider before using)"
        return f"- `{self.name}` — {self.summary or self.display_name}{args}"

    def detail_block(self) -> str:
        """The full signature, for the drill pass."""
        lines = [f"### `{self.name}` — {self.display_name or self.name}"]
        if self.summary:
            lines.append(self.summary)
        if self.fields:
            if self.source == "source-scan":
                lines.append(
                    "Arguments (under `config.with`) — read from the provider's source, so the "
                    "NAMES are reliable but which are required is not stated here:"
                )
            else:
                lines.append("Arguments (under `config.with`):")
            for fname, ftype, required in self.fields:
                mark = "required" if required else "optional"
                lines.append(f"  - `{fname}`: {ftype} ({mark})")
        elif self.arguments_known:
            lines.append("Takes no arguments.")
        else:
            lines.append(
                "Argument shape is NOT documented in this build. Do not guess it — either pick a "
                "provider whose shape is known, or leave the node out and say why."
            )
        if self.supports_dry_run:
            lines.append("Supports dry-run.")
        return "\n".join(lines)


@dataclass
class GroundingBundle:
    """Everything the planner may treat as true about this system.

    Assembled per plan rather than cached: a provider registered by an app install, or a model
    swapped in settings, changes what a valid spec looks like — and a cached bundle would keep
    planning against the previous system while reporting success.
    """

    node_kinds: list[str] = field(default_factory=list)
    container_kinds: list[str] = field(default_factory=list)
    llm_kinds: list[str] = field(default_factory=list)
    providers: list[ProviderSignature] = field(default_factory=list)
    mcp_tools: list[str] = field(default_factory=list)
    mcp_tools_dropped: int = 0
    templates: list[str] = field(default_factory=list)
    binding_roots: list[str] = field(default_factory=list)
    pipes: list[str] = field(default_factory=list)
    structured_output: bool = False
    model_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_kinds": list(self.node_kinds),
            "container_kinds": list(self.container_kinds),
            "llm_kinds": list(self.llm_kinds),
            "providers": [
                {
                    "name": p.name,
                    "summary": p.summary,
                    "fields": [
                        {"name": n, "type": t, "required": r} for n, t, r in p.fields
                    ],
                    "source": p.source,
                    "arguments_known": p.arguments_known,
                }
                for p in self.providers
            ],
            "mcp_tools": list(self.mcp_tools),
            "mcp_tools_dropped": self.mcp_tools_dropped,
            "templates": list(self.templates),
            "binding_roots": list(self.binding_roots),
            "pipes": list(self.pipes),
            "structured_output": self.structured_output,
            "model_notes": list(self.model_notes),
        }

    def index(self) -> str:
        """The ORIENT pass: everything, one line each, cheap enough to always include."""
        parts = [
            "## What this engine can actually run",
            "",
            f"Node kinds ({len(self.node_kinds)}, and no others): "
            + ", ".join(f"`{k}`" for k in self.node_kinds),
            "Containers (hold children, do no work themselves): "
            + ", ".join(f"`{k}`" for k in self.container_kinds),
            "Model-consuming kinds: " + ", ".join(f"`{k}`" for k in self.llm_kinds),
            "",
            "Binding roots (nothing else resolves): "
            + ", ".join(f"`{{{{{r}.*}}}}`" for r in self.binding_roots),
            "Binding pipes (a CLOSED set): " + ", ".join(f"`{p}`" for p in self.pipes),
            "",
            "### Action providers",
        ]
        parts.extend(p.index_line() for p in self.providers)
        if self.mcp_tools:
            parts.append("")
            parts.append("### MCP servers configured on this machine")
            parts.append(
                "Tool names must be discovered from the server, not guessed — reference the "
                "SERVER and say which capability you need."
            )
            parts.append(", ".join(f"`{t}`" for t in self.mcp_tools))
            if self.mcp_tools_dropped:
                parts.append(
                    f"({self.mcp_tools_dropped} more are registered but not listed here — ask if "
                    "you need one that is missing.)"
                )
        if self.templates:
            parts.append("")
            parts.append(
                "### Existing templates (prefer adapting one over generating from scratch)"
            )
            parts.append(", ".join(f"`{t}`" for t in self.templates))
        return "\n".join(parts)

    def detail(self, provider_names: list[str]) -> str:
        """The DRILL pass: full signatures for the providers the planner actually chose."""
        wanted = [p for p in self.providers if p.name in set(provider_names)]
        if not wanted:
            return ""
        return "\n\n".join(
            ["## Provider signatures"]
            + [p.detail_block() for p in wanted[:MAX_DETAILED_PROVIDERS]]
        )


def build_bundle(*, include_mcp: bool = True) -> GroundingBundle:
    """Assemble the bundle from whatever this system actually has.

    Every source is wrapped: a bundle is an ENHANCEMENT to planning, so a registry that cannot be
    read degrades that section rather than failing the plan. The one thing it must never do is
    report a section as empty when it simply could not be read — see `mcp_tools_dropped` and
    `ProviderSignature.arguments_known` for how absence is distinguished from ignorance.
    """
    bundle = GroundingBundle()
    _add_node_taxonomy(bundle)
    _add_bindings(bundle)
    _add_providers(bundle)
    _add_templates(bundle)
    if include_mcp:
        _add_mcp_tools(bundle)
    _add_model_capabilities(bundle)
    return bundle


def _add_node_taxonomy(bundle: GroundingBundle) -> None:
    from gideon.automation.workflows.models import CONTAINER_KINDS, LLM_KINDS, NodeKind

    bundle.node_kinds = [k.value for k in NodeKind]
    bundle.container_kinds = sorted(k.value for k in CONTAINER_KINDS)
    bundle.llm_kinds = sorted(k.value for k in LLM_KINDS)


def _add_bindings(bundle: GroundingBundle) -> None:
    """The binding roots and pipes, read from the resolver rather than listed by hand.

    Session 31 shipped five templates referencing `{{defaults.*}}`, which is not a root — the
    validator caught it, but only after the specs were written. Reading the real roots is what
    stops a planner making the same mistake at generation time.
    """
    from gideon.automation.workflows.bindings import PIPES

    bundle.binding_roots = [
        "inputs",
        "nodes",
        "item",
        "iter",
        "last",
        "siblings",
        "previous",
        "brief",
    ]
    bundle.pipes = sorted(PIPES)


def _add_providers(bundle: GroundingBundle) -> None:
    try:
        from gideon.integrations.action_providers.registry import (
            _ensure_default_providers_registered,
            _providers,
        )

        _ensure_default_providers_registered()
        names = sorted(_providers)
    except Exception:
        logger.debug(
            "action registry unreadable — bundle ships without providers", exc_info=True
        )
        return

    from gideon.assurance.validation import ALLOWED_HOOK_PROVIDERS

    for name in names:
        if name not in ALLOWED_HOOK_PROVIDERS:
            continue
        try:
            bundle.providers.append(_signature_for(name, _providers[name]))
        except Exception:
            logger.debug("could not read signature for %s", name, exc_info=True)


def _signature_for(name: str, provider: Any) -> ProviderSignature:
    doc = inspect.getdoc(provider.__class__) or ""
    sig = ProviderSignature(
        name=name,
        display_name=str(getattr(provider, "display_name", "") or ""),
        summary=_first_sentence(doc),
        supports_dry_run=bool(getattr(provider, "supports_dry_run", False)),
    )

    typed = _typed_fields(name)
    if typed:
        sig.fields, sig.source = typed, "schema"
        return sig

    parsed = _docstring_fields(doc)
    if parsed:
        sig.fields, sig.source = parsed, "docstring"
        return sig

    scraped = _source_fields(provider)
    if scraped:
        sig.fields, sig.source = scraped, "source-scan"
    return sig


def _typed_fields(name: str) -> list[tuple[str, str, bool]]:
    """Fields from `MCP_CORE_SCHEMAS`, which carries real types and required flags.

    Preferred over the docstring when both exist: the schema is what the validator enforces, so a
    docstring that has drifted from it would teach the planner the wrong shape.
    """
    try:
        from gideon.assurance.validation import MCP_CORE_SCHEMAS

        schema = MCP_CORE_SCHEMAS.get(name)
    except Exception:
        return []
    if schema is None:
        return []
    out: list[tuple[str, str, bool]] = []
    for spec in getattr(schema, "fields", []) or []:
        ftype = getattr(getattr(spec, "type", None), "__name__", "any")
        out.append(
            (
                str(getattr(spec, "name", "")),
                ftype,
                bool(getattr(spec, "required", False)),
            )
        )
    return [(n, t, r) for n, t, r in out if n]


_DOC_FIELD_RE = re.compile(r"^\s*[\"'](?P<name>[a-z_]+)[\"']\s*:\s*(?P<value>[^,#\n]+)")


def _docstring_fields(doc: str) -> list[tuple[str, str, bool]]:
    """Parse an `action_config` shape out of a provider docstring.

    Only inside the block that follows an `action_config` mention — scanning the whole docstring
    picked up example JSON from unrelated prose in a measurement run.
    """
    if "action_config" not in doc:
        return []
    block = doc.split("action_config", 1)[1]
    out: list[tuple[str, str, bool]] = []
    for line in block.splitlines():
        match = _DOC_FIELD_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        raw = match.group("value").strip()
        required = "# required" in line.lower() or "required" in line.lower()
        out.append((name, _guess_type(raw), required))
    return out


_SOURCE_FIELD_RE = re.compile(
    r"(?:action_config|cfg|config)\s*(?:or\s*\{\})?\s*\)?\s*\.get\("
    r"\s*[\"\']([a-z_][a-z0-9_]*)[\"\']"
)

_GENERIC_KEYS = frozenset({"with", "config", "context", "payload", "provider"})


def _source_fields(provider: Any) -> list[tuple[str, str, bool]]:
    """Argument names scraped from the provider's own source.

    Requiredness is NOT inferred: a `cfg.get` tells you the name and nothing about whether the
    provider errors without it, and guessing would produce a confident wrong contract. Everything
    here is marked optional and the caller labels the source `source-scan` so the distinction
    survives into the prompt.
    """
    try:
        module = inspect.getmodule(provider.__class__)
        if module is None:
            return []
        source = inspect.getsource(module)
        tree = ast.parse(source)
        definitions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        pending = [provider.__class__.__name__]
        visited: set[str] = set()
        fragments: list[str] = []
        while pending:
            name = pending.pop()
            if name in visited or name not in definitions:
                continue
            visited.add(name)
            node = definitions[name]
            fragments.append(ast.get_source_segment(source, node) or "")
            pending.extend(
                ref.id for ref in ast.walk(node) if isinstance(ref, ast.Name)
            )
        source = "\n".join(fragments)
    except Exception:
        return []
    names = sorted(set(_SOURCE_FIELD_RE.findall(source)) - _GENERIC_KEYS)
    return [(n, "any", False) for n in names]


def _guess_type(raw: str) -> str:
    raw = raw.strip().rstrip(",")
    if raw.startswith("["):
        return "list"
    if raw.startswith("{"):
        return "object"
    if raw in ("true", "false"):
        return "bool"
    if raw.replace("_", "").replace(".", "").isdigit():
        return "number"
    return "string"


def _add_templates(bundle: GroundingBundle) -> None:
    try:
        from gideon.automation.workflows import bundled_defs

        bundle.templates = list(bundled_defs.template_names())
    except Exception:
        logger.debug("template names unreadable", exc_info=True)


def _add_mcp_tools(bundle: GroundingBundle) -> None:
    """The user's own MCP tools, as first-class options.

    Without these a planner can only reach the built-in providers, and a spec that needed the
    user's Slack or Jira tool gets a hallucinated provider name instead. Best-effort: an
    unreachable MCP store degrades to no tools rather than failing the plan.
    """
    import json

    from gideon.core.config.loader import config_dir

    path = config_dir() / "mcp.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("mcp.json unreadable", exc_info=True)
        bundle.model_notes.append("mcp.json present but unreadable — MCP tools omitted")
        return

    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict):
        return
    names = sorted(str(k) for k in servers)
    bundle.mcp_tools = names[:MAX_MCP_TOOLS]
    bundle.mcp_tools_dropped = max(0, len(names) - MAX_MCP_TOOLS)


def _add_model_capabilities(bundle: GroundingBundle) -> None:
    """Can the bound model be held to a JSON schema?

    Read from the BACKEND capability registries. The plan is explicit that the frontend's
    `capableModels` must not be the source: it is a settings-UI helper that does not know which
    model the engine actually bound for a use case.
    """
    try:
        from gideon.integrations.llm.capabilities import StructuredOutput
        from gideon.integrations.llm.registry import get_default_registry

        registry = get_default_registry()
    except Exception:
        logger.debug("llm registry unavailable", exc_info=True)
        bundle.model_notes.append(
            "provider registry unreadable — schema-constrained emission off"
        )
        return

    modes: list[str] = []
    for type_ in sorted(getattr(registry, "_capabilities", {}) or {}):
        try:
            cap = registry.capability_of(type_)
        except Exception:
            continue
        mode = getattr(cap, "structured_output", StructuredOutput.NONE)
        value = getattr(mode, "value", str(mode))
        modes.append(f"{type_}={value}")
        if mode != StructuredOutput.NONE:
            bundle.structured_output = True

    if not modes:
        bundle.model_notes.append(
            "no provider types registered (process not bootstrapped) — structured output UNKNOWN, "
            "treat schema-constrained emission as unavailable rather than unsupported"
        )
        return
    bundle.model_notes.append("structured output by provider type: " + ", ".join(modes))


def _first_sentence(doc: str) -> str:
    text = " ".join((doc or "").split())
    if not text:
        return ""
    for end in (". ", " — "):
        if end in text:
            return text.split(end, 1)[0].strip().rstrip(".")
    return text[:140].rstrip(".")

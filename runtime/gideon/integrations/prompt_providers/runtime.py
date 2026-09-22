"""Runtime rendering helpers — the one way call sites turn a bound prompt into
final text.

Every migrated call site that used to build a hardcoded f-string now calls
:func:`render_use_case_prompt` (for a standalone LLM task prompt bound to a
use-case) or :func:`render_snippet_block` (for an injected instruction fragment).
Both resolve through the registered prompt provider and render through the engine
— snippet ``{{> name}}`` includes and ``{{var}}`` substitutions are applied
exactly as in the Settings preview, so what the model receives can never drift
from what an author sees.

These wrap the lower-level ``engine.render`` so no consumer re-implements the
provider lookup + snippet-resolver + ``PromptTemplate`` assembly.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from gideon.integrations.prompt_providers.base import (
    PromptSnippet,
    PromptTemplate,
    PromptVariable,
)

logger = logging.getLogger(__name__)


def _variables_with_inline(
    content: str,
    declared: list[PromptVariable],
    values: dict[str, Any],
) -> list[PromptVariable]:
    from gideon.integrations.prompt_providers.engine import extract_inline_variables

    seen = {variable.name for variable in declared}
    variables = [
        *declared,
        *(
            variable
            for variable in extract_inline_variables(content)
            if variable.name not in seen
        ),
    ]
    seen.update(variable.name for variable in variables)
    variables.extend(
        PromptVariable(name=name)
        for name in values
        if isinstance(name, str) and name not in seen
    )
    return variables


def _render_with_inline(
    template: PromptTemplate | PromptSnippet,
    values: dict[str, Any],
    resolver: Callable[[str], "PromptSnippet | None"],
) -> str:
    from gideon.integrations.prompt_providers.engine import render

    return render(
        template.content,
        _variables_with_inline(template.content, template.variables, values),
        values,
        resolver=resolver,
    )


def snippet_resolver() -> Callable[[str], "PromptSnippet | None"]:
    """A ``name -> PromptSnippet | None`` resolver for the engine's ``{{> name}}``
    includes, backed by the default prompt provider. Yields None for every name
    when no provider is available (the engine then renders an explicit
    ``[missing snippet: name]`` marker rather than failing)."""
    try:
        from gideon.integrations.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_default_provider,
        )

        _ensure_default_providers_registered()
        provider = get_default_provider()
    except Exception:
        provider = None
    return lambda n: provider.get_snippet(n) if provider is not None else None


def render_use_case_prompt(
    use_case: str, values: dict[str, Any] | None = None
) -> str | None:
    """Resolve the prompt bound to ``use_case`` and render it with ``values``.

    Reads the binding (Settings → Prompts) or the bundled default, fetches the
    template from its provider, and renders it through the engine with snippet
    includes resolved. Returns ``None`` only when neither the binding nor its
    declared bundled fallback can be resolved.
    """
    from gideon.extensions.providers.prompt_use_cases import (
        resolve_prompt_declaration,
    )

    try:
        declaration = resolve_prompt_declaration(use_case)
        if declaration is None:
            return None
        return _render_with_inline(
            declaration.template,
            values or {},
            resolver=declaration.provider.get_snippet,
        )
    except Exception:
        logger.debug("render_use_case_prompt failed for %r", use_case, exc_info=True)
        return None


def render_snippet_block(name: str, values: dict[str, Any] | None = None) -> str:
    """Render a bundled instruction snippet by name, with ``values`` substituted.

    Used for injected instruction fragments (critical rules, workspace identity,
    widget block, …) that are composed into the session context rather than bound
    to a use-case. Returns ``""`` when the snippet can't be resolved so a missing
    fragment degrades to nothing instead of breaking context assembly.
    """
    try:
        from gideon.integrations.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_default_provider,
        )

        _ensure_default_providers_registered()
        provider = get_default_provider()
        if provider is None:
            return ""
        snip = provider.get_snippet(name)
        if snip is None:
            return ""
        return _render_with_inline(
            snip, values or {}, resolver=(lambda n: provider.get_snippet(n))
        )
    except Exception:
        logger.debug("render_snippet_block failed for %r", name, exc_info=True)
        return ""


def render_persona_configuration(name: str) -> str:
    """Render a configured bundled persona snippet.

    Persona names cross the dashboard request boundary, so they are restricted to
    the shipped ``persona-*`` catalog entries before reaching the general snippet
    renderer. The provider still supplies the content, preserving edits made in
    Settings → Prompts while preventing an arbitrary snippet name from becoming a
    model instruction through chat configuration.
    """
    configured = str(name or "").strip()
    if not configured:
        return ""

    try:
        from gideon.integrations.prompt_providers.catalog import BUNDLED_SNIPPETS

        persona_names = {
            entry.name
            for entry in BUNDLED_SNIPPETS
            if entry.name.startswith("persona-")
        }
        if configured not in persona_names:
            logger.warning("Ignoring unknown persona configuration %r", configured)
            return ""
        return render_snippet_block(configured)
    except Exception:
        logger.debug(
            "render_persona_configuration failed for %r", configured, exc_info=True
        )
        return ""

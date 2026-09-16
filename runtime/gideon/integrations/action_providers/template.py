"""Render native action text with a single sanitized payload substitution boundary."""

from __future__ import annotations

from string import Template

from gideon.integrations.action_providers.base import ActionContext

STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {
        "trigger_id",
        "trigger_name",
        "kind",
        "session_key",
        "chain_depth",
        "refresh_number",
        "new_count",
        "manual",
        "dedup",
        "source_trigger_id",
        "__hook_depth",
    }
)


def _template_values(ctx: ActionContext) -> dict[str, str]:
    from gideon.security.security import strip_role_tokens

    values = dict(
        EVENT=strip_role_tokens(ctx.event), CONTEXT=strip_role_tokens(ctx.context)
    )
    content = ctx.payload or {}
    values.update(
        (name, str(value) if name in STRUCTURAL_KEYS else strip_role_tokens(str(value)))
        for name, value in content.items()
    )
    return values


def render_template(tmpl: str, ctx: ActionContext) -> str:
    if not tmpl:
        return ""
    values = _template_values(ctx)

    def replacement(match):
        named, braced, escaped, invalid = (
            match.group(group) for group in ("named", "braced", "escaped", "invalid")
        )
        key = named or braced
        if key is not None:
            return values[key] if key in values else match.group()
        if escaped is not None:
            return "$"
        if invalid is not None:
            return match.group()
        raise ValueError("unknown template token")

    try:
        return Template.pattern.sub(replacement, tmpl)
    except Exception:
        return tmpl
